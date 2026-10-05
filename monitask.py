#!/opt/homebrew/opt/python@3.13/bin/python3.13
"""Monitask state engine (local Period files), run by run.sh under the bundled python.

Commands:
  check         hourly check (launchd): decide, then notify (remind / warn_unknown) or press
                Play at a session start / Pause at lunch
  status        key=value lines (accessibility_trusted, running, state, gui_session)
  dump-windows  print window / control structure

Reads state without AX; Play/Pause (set_playing) still requires AX and only runs when
check.decide() says so. AX confirms button actions immediately; Period updates can lag.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# pyobjc comes from the unpacked wheels (launchd also sets PYTHONPATH; belt and braces)
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "pylibs")]

import AppKit
import ApplicationServices as AX
from Foundation import NSDate, NSRunLoop
import Quartz

from monitask_center import check, config, i18n, notify, workday
from monitask_center.status import action_text, reason_text, state_text
from monitask_center.i18n import t

APP_NAME = "Monitask"
AX_API_DISABLED = -25211
AX_TIMEOUT_S = 10.0
LAUNCH_TIMEOUT_S = 30   # max wait after `open -a` for the Play/Pause controls to appear
VERIFY_TIMEOUT_S = 15   # max wait after pressing for the state to flip
POLL_S = 0.5
PERIOD_MAX_AGE_S = 180  # allow two missed minute updates; calibrate after a real pause observation

_last_error = ""  # why the last read_state() returned "unknown"


def gui_session_available() -> bool:
    session = Quartz.CGSessionCopyCurrentDictionary()
    if not session:
        return False
    return bool(session.get("kCGSSessionOnConsoleKey")) and not bool(
        session.get("CGSSessionScreenIsLocked", False))


def refresh_running_applications() -> None:
    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.1))


def running_app():
    refresh_running_applications()
    return next((a for a in AppKit.NSWorkspace.sharedWorkspace().runningApplications()
                 if a.localizedName() == APP_NAME), None)


def ax_value(element, attribute: str):
    error, value = AX.AXUIElementCopyAttributeValue(element, attribute, None)
    if error == AX_API_DISABLED:
        raise RuntimeError(t("engine.ax_untrusted", path=os.path.realpath(sys.executable)))
    if error != 0:
        return None
    return value


def ax_app_element(app):
    element = AX.AXUIElementCreateApplication(app.processIdentifier())
    AX.AXUIElementSetMessagingTimeout(element, AX_TIMEOUT_S)
    return element


def _collect(element, role: str, out: list) -> None:
    if ax_value(element, "AXRole") == role:
        out.append(element)
    error, children = AX.AXUIElementCopyAttributeValue(element, "AXChildren", None)
    if error == 0 and children:
        for c in children:
            _collect(c, role, out)


def windows(app) -> list:
    return list(ax_value(ax_app_element(app), "AXWindows") or [])


def checkboxes(app) -> list:
    out: list = []
    for w in windows(app):
        _collect(w, "AXCheckBox", out)
    return out


def _is_timing(play, pause) -> bool:
    if ax_value(play, "AXValue"):
        return True
    # A panel reopened while tracking shows a stale Play value of 0; Play disabled + Pause
    # enabled is the reliable "tracking" signal.
    return not ax_value(play, "AXEnabled") and bool(ax_value(pause, "AXEnabled"))


def read_state() -> str:
    """not-running | unknown | timing | paused. Read-only; no AX permission needed."""
    global _last_error
    _last_error = ""
    if running_app() is None:
        return "not-running"
    try:
        periods = Path.home() / "Library/Application Support/Monitask/Periods"
        latest = max((p for p in periods.iterdir() if p.suffix == ".json"),
                     key=lambda p: p.stat().st_mtime_ns, default=None)
        if latest is None:
            raise ValueError("no period files")
        data = json.loads(latest.read_text(encoding="utf-8"))
        active = datetime.fromisoformat(data["DateLastActive"].replace("Z", "+00:00"))
        if active.utcoffset() is None:
            raise ValueError("timestamp has no timezone")
        age = (datetime.now(timezone.utc) - active).total_seconds()
        if age < 0:
            raise ValueError("timestamp is in the future")
        # ponytail: freshness infers timing with up to 3 min pause lag; use a direct signal if available.
        return "timing" if age < PERIOD_MAX_AGE_S else "paused"
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        _last_error = t("engine.period_error", error=type(exc).__name__)
        return "unknown"


def open_app() -> None:
    subprocess.run(["/usr/bin/open", "-a", APP_NAME], timeout=10, check=False)


def set_playing(playing: bool) -> bool:
    """Press Play (True) or Pause (False) and confirm the state flipped.

    Opens Monitask first when it is closed or hiding its window (it hides while timing), once,
    then waits up to LAUNCH_TIMEOUT_S for the two controls. Raises RuntimeError if AX is untrusted."""
    if not AX.AXIsProcessTrusted():
        raise RuntimeError(t("engine.ax_untrusted", path=os.path.realpath(sys.executable)))
    deadline = time.monotonic() + LAUNCH_TIMEOUT_S
    opened = False
    while time.monotonic() < deadline:
        app = running_app()
        boxes = checkboxes(app) if app else []
        if len(boxes) >= 2:
            break
        if not opened:
            open_app()
            opened = True
        time.sleep(POLL_S)
    else:
        return False
    # Two controls, not one toggle: [0] is Play, [1] is Pause; the active one is disabled.
    AX.AXUIElementPerformAction(boxes[0 if playing else 1], "AXPress")
    deadline = time.monotonic() + VERIFY_TIMEOUT_S
    while time.monotonic() < deadline:
        app = running_app()
        if app is not None:
            if playing and not windows(app):  # Monitask hides its window once timing
                return True
            boxes = checkboxes(app)
            if len(boxes) >= 2 and _is_timing(boxes[0], boxes[1]) == playing:
                return True
        time.sleep(POLL_S)
    return False


def press(action: str, telegram: bool) -> str:
    """Run a play/pause decision and notify the outcome -> action, or action_failed."""
    try:
        ok = set_playing(action == "play")
    except Exception as exc:  # noqa: BLE001 - a failed press must still notify, never crash the tick
        notify.log(t("check.err.exception", error=exc))
        ok = False
    if action == "play":
        message = t("check.play_ok") if ok else t("check.play_failed")
    else:
        message = t("check.pause_ok") if ok else t("check.pause_failed")
    notify.notify(message, telegram)
    return action if ok else f"{action}_failed"


def cmd_check() -> int:
    os.environ["MONITASK_CENTER_LOG_STDOUT"] = "1"  # launchd stdout keeps its lines
    settings = config.load()
    i18n.set_language(settings.language)
    hour = datetime.now().hour
    # Pure window gate first: no calendar access (download / warning) and no state read.
    action = check.decide(hour, settings, (True, "workday"), "timing")
    wd, state, reason = None, "n/a", t("check.reason_unchecked")
    if action != "outside":
        wd = workday.workday_status(date.today(), settings)
        reason = reason_text(wd[1])
        action = check.decide(hour, settings, wd, "timing")
        if action in ("ok", "pause"):  # lunch needs the real state to know whether to pause
            if not gui_session_available():
                notify.log(t("check.locked"))
                check.record_last_check("locked", "n/a")  # quiet and read-only, but not stale
                return 0
            state = read_state()
            action = check.decide(hour, settings, wd, state)
    notify.log(t("check.decision", hour=hour, action=action_text(action),
                 state=state_text(state), reason=reason))
    if action == "remind":
        key = "check.remind_paused" if state == "paused" else "check.remind_not_running"
        notify.notify(t(key), settings.telegram)
    elif action == "warn_unknown":
        notify.notify(t("check.warn_unknown", error=_last_error or state), settings.telegram)
    elif action in ("play", "pause"):
        action = press(action, settings.telegram)
    check.record_last_check(action, state)
    return 0


def cmd_status() -> int:
    app = running_app()
    print(f"accessibility_trusted={bool(AX.AXIsProcessTrusted())}")
    print(f"running={app is not None}")
    print(f"state={read_state()}")
    print(f"gui_session={gui_session_available()}")
    return 0


def _button_label(element) -> str:
    for attr in ("AXTitle", "AXDescription", "AXValue"):
        v = ax_value(element, attr)
        if v:
            return str(v)
    return ""


def cmd_dump_windows() -> int:
    app = running_app()
    if app is None:
        print(t("engine.not_running"))
        return 0
    for i, w in enumerate(windows(app)):
        print(t("engine.window", index=i, title=repr(ax_value(w, "AXTitle")),
                subrole=repr(ax_value(w, "AXSubrole"))))
        boxes: list = []
        _collect(w, "AXCheckBox", boxes)
        btns: list = []
        _collect(w, "AXButton", btns)
        print(t("engine.window_detail", boxes=len(boxes), buttons=[_button_label(b) for b in btns]))
    return 0


COMMANDS = {"check": cmd_check, "status": cmd_status, "dump-windows": cmd_dump_windows}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        print(t("engine.usage", prog=sys.argv[0], commands="|".join(COMMANDS)), file=sys.stderr)
        return 2
    cmd = sys.argv[1]
    if cmd == "check":  # one check at a time; a second concurrent tick just exits
        config.data_dir().mkdir(parents=True, exist_ok=True)
        lock = (config.data_dir() / "automation.lock").open("w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
    return COMMANDS[cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
