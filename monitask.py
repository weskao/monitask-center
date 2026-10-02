#!/opt/homebrew/opt/python@3.13/bin/python3.13
"""Read-only Monitask state engine (pyobjc / AX), run by run.sh under the bundled python.

Commands:
  check         hourly check (launchd): decide, then notify only for remind / warn_unknown
  status        key=value lines (accessibility_trusted, running, state, gui_session)
  dump-windows  print window / control structure

Never starts, stops, launches or clicks anything: it only reads state.
The AX helpers handle a locked screen, cold start and the NSWorkspace cache.
"""

from __future__ import annotations

import fcntl
import os
import sys
from datetime import date, datetime
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


def read_state() -> str:
    """not-running | unknown | hidden | timing | paused. Strictly read-only."""
    global _last_error
    _last_error = ""
    app = running_app()
    if app is None:
        return "not-running"
    try:
        if not AX.AXIsProcessTrusted():
            _last_error = t("engine.ax_untrusted", path=os.path.realpath(sys.executable))
            return "unknown"
        wins = windows(app)
        if not wins:  # Monitask hides its window while timing
            return "hidden"
        boxes: list = []
        for w in wins:
            _collect(w, "AXCheckBox", boxes)
        if len(boxes) < 2:
            _last_error = t("check.err.checkboxes", count=len(boxes))
            return "unknown"
        return "timing" if bool(ax_value(boxes[0], "AXValue")) else "paused"
    except Exception as exc:  # noqa: BLE001 - any AX failure is "unknown", never a crash
        _last_error = t("check.err.exception", error=exc)
        return "unknown"


def cmd_check() -> int:
    os.environ["MONITASK_CENTER_LOG_STDOUT"] = "1"  # launchd stdout keeps its lines
    settings = config.load()
    i18n.set_language(settings.language)
    hour = datetime.now().hour
    # Pure window/lunch gates first: no calendar access (download / warning) and no AX read.
    action = check.decide(hour, settings, (True, "workday"), "timing")
    wd, state, reason = None, "n/a", t("check.reason_unchecked")
    if action not in ("outside", "skip_lunch"):
        wd = workday.workday_status(date.today(), settings)
        reason = reason_text(wd[1])
        action = check.decide(hour, settings, wd, "timing")
        if action == "ok":
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
