"""Monitask state engine (local Period files): `python -m monitask_center.engine`, run by run.sh
under the python copy inside MonitaskCenter.app.

Commands:
  check         hourly check (launchd): decide, then notify (remind / warn_unknown) or press
                Play at a session start / Pause at lunch
  overtime      daily overtime prompt (launchd): still timing on a workday -> ask stop / keep
                working; no answer in 15 min -> Pause; keep working -> ask again in 30 min
  status       key=value lines (accessibility_trusted, running, state, gui_session)
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

import AppKit
import ApplicationServices as AX
import Quartz
from Foundation import NSDate, NSRunLoop

from monitask_center import check, config, i18n, launchd, notify, workday
from monitask_center.i18n import t
from monitask_center.status import action_text, reason_text, state_text

APP_NAME = "Monitask"
AX_API_DISABLED = -25211
AX_TIMEOUT_S = 10.0
LAUNCH_TIMEOUT_S = 30   # max wait after `open -a` for the Play/Pause controls to appear
VERIFY_TIMEOUT_S = 15   # max wait after pressing for the state to flip
POLL_S = 0.5
PERIOD_MAX_AGE_S = 180  # allow two missed minute updates; calibrate after a real pause observation
OVERTIME_ANSWER_S = 15 * 60   # no answer to the overtime prompt within this -> Pause
OVERTIME_RECHECK_S = 30 * 60  # "keep working" -> ask again after this
# Text goes in as argv, never spliced into the source: no AppleScript quoting to get wrong.
OVERTIME_DIALOG = """on run argv
activate
set d to display dialog (item 1 of argv) buttons {item 2 of argv, item 3 of argv} \
default button (item 3 of argv) with title "Monitask" giving up after ((item 4 of argv) as integer)
if gave up of d then return ""
return button returned of d
end run"""

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
        stats = ((p.stat(), p) for p in periods.iterdir() if p.suffix == ".json")
        _, latest = max(((st.st_mtime_ns, p) for st, p in stats if st.st_size), default=(0, None))
        if latest is None:  # Monitask deletes uploaded periods: none open (or only 0-byte leftovers) = not timing
            return "paused"
        data = json.loads(latest.read_text(encoding="utf-8"))
        end = datetime.fromisoformat(data["DateEnd"].replace("Z", "+00:00"))
        if end.utcoffset() is None:
            raise ValueError("timestamp has no timezone")
        age = (datetime.now(timezone.utc) - end).total_seconds()
        if age < 0:
            raise ValueError("timestamp is in the future")
        # DateEnd advances while timing; DateLastActive only advances on user input.
        # shortcut: freshness has up to 3 min pause lag, use a direct signal when available.
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
        # A rebuilt ad-hoc runner no longer matches its old grant; the prompt lists the current one.
        AX.AXIsProcessTrustedWithOptions({AX.kAXTrustedCheckOptionPrompt: True})
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


def _try_set_playing(playing: bool) -> bool:
    try:
        return set_playing(playing)
    except Exception as exc:  # noqa: BLE001 - a failed press must still notify, never crash the tick
        notify.log(t("check.err.exception", error=exc))
        return False


def press(action: str, telegram: bool) -> str:
    """Run a play/pause decision and notify the outcome -> action, or action_failed."""
    ok = _try_set_playing(action == "play")
    if action == "play":
        message = t("check.play_ok") if ok else t("check.play_failed")
    else:
        message = t("check.pause_ok") if ok else t("check.pause_failed")
    notify.log(message)
    notify.notify(message, telegram)
    return action if ok else f"{action}_failed"


def cmd_check() -> int:
    os.environ["MONITASK_CENTER_LOG_STDOUT"] = "1"  # launchd stdout keeps its lines
    settings = config.load()
    i18n.set_language(settings.language)
    if note := launchd.install_missing(settings):  # hourly, so an upgrade gets its new jobs unattended
        notify.log(note)
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


def ask_overtime() -> str:
    """Show the overtime dialog -> "stop" | "continue" | "" (no answer in OVERTIME_ANSWER_S).

    A dialog that cannot be shown still waits out the answer window, so the user gets the
    time the notice promised before the timer is paused."""
    stop, keep = t("overtime.button_stop"), t("overtime.button_continue")
    deadline = time.monotonic() + OVERTIME_ANSWER_S
    try:
        r = subprocess.run(["/usr/bin/osascript", "-e", OVERTIME_DIALOG, t("overtime.dialog"),
                            stop, keep, str(OVERTIME_ANSWER_S)],
                           text=True, capture_output=True, timeout=OVERTIME_ANSWER_S + 30, check=False)
        answer, failed = (r.stdout or "").strip(), r.returncode != 0
        detail = (r.stderr or "").strip()
    except (OSError, subprocess.SubprocessError) as exc:
        answer, failed, detail = "", True, str(exc)
    if failed:
        notify.log(t("overtime.dialog_failed", error=detail))
        time.sleep(max(0.0, deadline - time.monotonic()))
    return {stop: "stop", keep: "continue"}.get(answer, "")


def cmd_overtime() -> int:
    os.environ["MONITASK_CENTER_LOG_STDOUT"] = "1"  # launchd stdout keeps its lines
    settings = config.load()
    i18n.set_language(settings.language)
    if not settings.overtime_prompt:
        notify.log(t("overtime.disabled"))
        return 0
    is_workday, reason = workday.workday_status(date.today(), settings)
    if not is_workday:
        notify.log(t("overtime.skip_day", reason=reason_text(reason)))
        return 0
    if not gui_session_available():
        notify.log(t("overtime.locked"))
        return 0
    asked = False
    while (state := read_state()) in check.QUIET_STATES:
        if not asked:
            notify.notify(t("overtime.notice", time=config.clock(settings.overtime_minutes),
                            minutes=OVERTIME_ANSWER_S // 60), settings.telegram)
            asked = True
        answer = ask_overtime()
        if answer == "continue":
            message = t("overtime.continue", minutes=OVERTIME_RECHECK_S // 60)
            notify.log(message)
            notify.notify(message, settings.telegram)
            time.sleep(OVERTIME_RECHECK_S)
            continue
        why = t("overtime.why_stop" if answer == "stop" else "overtime.why_timeout")
        if (state := read_state()) not in check.QUIET_STATES:  # stopped by hand meanwhile
            message = t("overtime.no_pause", why=why, state=state_text(state))
            notify.log(message)
            notify.notify(message, settings.telegram)
            break
        notify.log(why)
        ok = _try_set_playing(False)
        message = t("overtime.paused" if ok else "overtime.pause_failed", why=why)
        notify.log(message)
        notify.notify(message, settings.telegram)
        return 0
    notify.log(t("overtime.not_timing", state=state_text(state)))
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


COMMANDS = {"check": cmd_check, "overtime": cmd_overtime, "status": cmd_status,
            "dump-windows": cmd_dump_windows}


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
