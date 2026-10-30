import importlib.util
import json
import os
import sys
import types
from datetime import date, datetime, timezone
from datetime import time as dt_time
from pathlib import Path

import pytest

from monitask_center.i18n import t as _t
from monitask_center.workday import workday_status as REAL_WORKDAY_STATUS

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "src/monitask_center/engine.py"


class Node:
    def __init__(self, role, children=(), value=None, windows=None, enabled=None):
        self.attrs = {"AXRole": role, "AXChildren": list(children), "AXValue": value}
        if enabled is not None:
            self.attrs["AXEnabled"] = enabled
        if windows is not None:
            self.attrs["AXWindows"] = windows


class FakeApp:
    def localizedName(self):
        return "Monitask"

    def processIdentifier(self):
        return 1


@pytest.fixture
def engine(monkeypatch):
    """engine.py imported against fake pyobjc modules (pyobjc is not installable in the uv env)."""
    world = types.SimpleNamespace(apps=[FakeApp()], trusted=True, windows=[], pressed=[], on_press=None,
                                  session={"kCGSSessionOnConsoleKey": True, "CGSSessionScreenIsLocked": False})
    appkit = types.ModuleType("AppKit")
    appkit.NSWorkspace = types.SimpleNamespace(sharedWorkspace=lambda: types.SimpleNamespace(
        runningApplications=lambda: world.apps))
    ax = types.ModuleType("ApplicationServices")
    ax.AXIsProcessTrusted = lambda: world.trusted
    world.prompts = []
    ax.kAXTrustedCheckOptionPrompt = "AXTrustedCheckOptionPrompt"
    ax.AXIsProcessTrustedWithOptions = lambda opts: world.prompts.append(opts) or world.trusted
    ax.AXUIElementCreateApplication = lambda pid: Node("AXApplication", windows=world.windows)
    ax.AXUIElementSetMessagingTimeout = lambda el, s: 0

    def press(el, action):
        world.pressed.append((el, action))
        if world.on_press:
            world.on_press(el)
        return 0
    ax.AXUIElementPerformAction = press
    ax.AXUIElementCopyAttributeValue = lambda el, attr, _: (
        (0, el.attrs[attr]) if attr in el.attrs else (-25205, None))
    found = types.ModuleType("Foundation")
    found.NSDate = types.SimpleNamespace(dateWithTimeIntervalSinceNow_=lambda s: None)
    found.NSRunLoop = types.SimpleNamespace(currentRunLoop=lambda: types.SimpleNamespace(
        runUntilDate_=lambda d: None))
    quartz = types.ModuleType("Quartz")
    quartz.CGSessionCopyCurrentDictionary = lambda: world.session
    for mod in (appkit, ax, found, quartz):
        monkeypatch.setitem(sys.modules, mod.__name__, mod)
    spec = importlib.util.spec_from_file_location("monitask_engine", ENGINE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.world = world
    return module


def test_source_never_clicks_the_dock():
    assert '"Dock"' not in ENGINE.read_text(encoding="utf-8")


def test_read_state_not_running(engine):
    engine.world.apps = []
    assert engine.read_state() == "not-running"


@pytest.mark.parametrize("timestamp,expected", [
    ("2026-01-02T12:00:00.0000000Z", "timing"),
    ("2026-01-02T11:57:01+00:00", "timing"),
    ("2026-01-02T11:57:00Z", "paused"),
    ("2026-01-02T11:56:00Z", "paused"),
    ("2026-01-02T19:59:59.1234567+08:00", "timing"),
])
def test_read_state_uses_latest_period_without_accessibility(engine, timestamp, expected):
    engine.world.trusted = False
    periods = Path.home() / "Library/Application Support/Monitask/Periods"
    periods.mkdir(parents=True)
    now = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    engine.datetime = Clock
    old = periods / "z-old.json"
    old.write_text(json.dumps({"DateEnd": "2026-01-02T11:59:59.1234567Z"}))
    os.utime(old, (1, 1))
    latest = periods / "a-latest.json"
    latest.write_text(json.dumps({"DateLastActive": "2026-01-02T11:50:00Z",
                                  "DateEnd": timestamp}))
    assert engine.read_state() == expected
    assert engine._last_error == ""


@pytest.mark.parametrize("body", ['{', '[]', '{}', '{"DateEnd": null}',
                                  '{"DateEnd": "invalid"}',
                                  '{"DateEnd": "2026-01-02T12:00:00"}',
                                  '{"DateEnd": "2999-01-01T00:00:00Z"}'])
def test_read_state_invalid_latest_period_is_unknown(engine, body):
    periods = Path.home() / "Library/Application Support/Monitask/Periods"
    periods.mkdir(parents=True)
    old = periods / "old.json"
    old.write_text(json.dumps({"DateEnd": datetime.now(timezone.utc).isoformat()}))
    os.utime(old, (1, 1))
    (periods / "period.json").write_text(body)
    assert engine.read_state() == "unknown"
    assert engine._last_error and not engine._last_error.startswith("engine.")


def test_read_state_empty_period_directory_is_paused(engine):
    (Path.home() / "Library/Application Support/Monitask/Periods").mkdir(parents=True)
    assert engine.read_state() == "paused"
    assert engine._last_error == ""


def test_read_state_ignores_zero_byte_period_leftover(engine):
    periods = Path.home() / "Library/Application Support/Monitask/Periods"
    periods.mkdir(parents=True)
    (periods / "leftover_auto.json").write_text("")
    assert engine.read_state() == "paused"
    old = periods / "old.json"
    old.write_text(json.dumps({"DateEnd": datetime.now(timezone.utc).isoformat()}))
    os.utime(old, (1, 1))
    assert engine.read_state() == "timing"  # the newer 0-byte file does not hide real data


def test_read_state_missing_directory_is_unknown(engine):
    assert engine.read_state() == "unknown"
    assert engine._last_error


@pytest.fixture
def routed(engine, monkeypatch):
    """cmd_check with the clock, calendar and notifier replaced by recorders."""
    sent = []
    reads = []
    monkeypatch.setattr(engine.notify, "notify", lambda msg, tg=True: sent.append((msg, tg)))
    monkeypatch.setattr(engine.notify, "log", lambda msg: None)
    monkeypatch.setattr(engine.workday, "workday_status", lambda d, s, **kw: engine.world.wd)
    engine.world.wd = (True, "workday")
    engine.world.state = "timing"
    engine.world.hour, engine.world.minute = 14, 0

    def fake_read():
        reads.append(1)
        return engine.world.state
    monkeypatch.setattr(engine, "read_state", fake_read)
    presses = []
    engine.world.press_ok = True
    monkeypatch.setattr(engine, "set_playing", lambda playing: presses.append(playing) or engine.world.press_ok)
    engine.presses = presses

    class Clock:
        @staticmethod
        def now():
            return datetime.combine(date.today(), dt_time(engine.world.hour, engine.world.minute))
    monkeypatch.setattr(engine, "datetime", Clock)
    engine.sent, engine.reads = sent, reads
    return engine


@pytest.mark.parametrize("state,notified", [
    ("timing", 0), ("hidden", 0), ("paused", 1), ("not-running", 1), ("unknown", 1)])
def test_cmd_check_routes_states(routed, state, notified):
    from monitask_center import check
    routed.world.state = state
    assert routed.cmd_check() == 0
    assert len(routed.sent) == notified
    assert check.last_check()["state"] == state


def test_cmd_check_telegram_setting_is_honoured(routed):
    from monitask_center import config
    config.save(config.Settings(telegram=False))
    routed.world.state = "paused"
    routed.cmd_check()
    assert routed.sent[0][1] is False


@pytest.mark.parametrize("wd,hour,action", [
    ((False, "vacation"), 14, "skip_vacation"),
    ((False, "holiday:weekend"), 14, "skip_holiday"),
    ((True, "workday"), 8, "outside"),
    ((False, "vacation"), 12, "skip_vacation"),
])
def test_cmd_check_skips_do_not_touch_monitask(routed, wd, hour, action):
    from monitask_center import check
    routed.world.wd, routed.world.hour, routed.world.state = wd, hour, "paused"
    routed.cmd_check()
    assert routed.sent == [] and routed.reads == []
    assert check.last_check()["action"] == action


def test_cmd_check_locked_screen_does_no_ui_work(routed):
    from monitask_center import check
    routed.world.session = {"kCGSSessionOnConsoleKey": True, "CGSSessionScreenIsLocked": True}
    routed.world.state = "paused"
    assert routed.cmd_check() == 0
    assert routed.sent == [] and routed.reads == []
    last = check.last_check()
    assert (last["action"], last["state"]) == ("locked", "n/a")


def test_status_prints_key_values(engine, capsys):
    periods = Path.home() / "Library/Application Support/Monitask/Periods"
    periods.mkdir(parents=True)
    (periods / "period.json").write_text(json.dumps({"DateEnd": datetime.now(timezone.utc).isoformat()}))
    engine.cmd_status()
    out = dict(line.split("=") for line in capsys.readouterr().out.split())
    assert out == {"accessibility_trusted": "True", "running": "True",
                   "state": "timing", "gui_session": "True"}


def test_cmd_check_enables_stdout_logging(routed, monkeypatch):
    monkeypatch.delenv("MONITASK_CENTER_LOG_STDOUT", raising=False)
    routed.cmd_check()
    import os
    assert os.environ["MONITASK_CENTER_LOG_STDOUT"] == "1"
    monkeypatch.delenv("MONITASK_CENTER_LOG_STDOUT")


@pytest.fixture
def offline(routed, monkeypatch):
    """Real workday_status, empty holiday cache, downloads fail; records downloads and sends."""
    from monitask_center import workday
    monkeypatch.setattr(routed.workday, "workday_status", REAL_WORKDAY_STATUS)
    downloads = []

    def no_network(year, dest):
        downloads.append(year)
        raise OSError("offline")
    monkeypatch.setattr(workday, "_download", no_network)
    monkeypatch.setattr(workday, "notify", lambda msg, tg=True: routed.sent.append((msg, tg)))
    routed.downloads = downloads
    return routed


@pytest.mark.parametrize("hour,action", [(9, "outside"), (19, "outside")])
def test_cmd_check_gates_run_before_calendar(offline, hour, action):
    from monitask_center import check
    offline.world.hour = hour
    assert offline.cmd_check() == 0
    assert offline.sent == [] and offline.downloads == [] and offline.reads == []
    assert check.last_check()["action"] == action


def _leave(span):
    from monitask_center import config
    config.save(config.Settings(vacation=(f"{date.today().isoformat()} {span}",)))


def test_cmd_check_is_silent_inside_a_time_range_leave(offline):
    from monitask_center import check
    _leave("13:30~15:00")
    offline.world.state = "paused"
    offline.cmd_check()  # 14:00
    assert offline.sent == [] and offline.reads == [] and offline.downloads == []
    assert check.last_check()["action"] == "skip_vacation"


def test_cmd_check_is_normal_again_once_the_leave_ends(offline):
    from monitask_center import check
    _leave("13:30~15:00")
    offline.world.hour, offline.world.state = 15, "paused"
    offline.cmd_check()
    assert offline.reads and check.last_check()["action"] == "remind"


def test_cmd_check_fail_open_warning_after_gates(offline):
    from monitask_center import check
    offline.world.hour = 13
    offline.cmd_check()
    assert offline.downloads and len(offline.sent) == 1  # fail-open warning, once
    assert check.last_check()["action"] == "ok"


def test_cmd_check_decision_log_is_translated(routed, monkeypatch):
    logged = []
    monkeypatch.setattr(routed.notify, "log", logged.append)
    routed.cmd_check()
    line = logged[-1]
    assert "decision=" not in line and "=ok" not in line and "=timing" not in line
    assert "check.decision" not in line and "workday=workday" not in line
    from monitask_center import status
    assert status.state_text("timing") in line and status.action_text("ok") in line


def controls(play_value, play_enabled, pause_enabled):
    """A Monitask window as AX shows it: [0] Play, [1] Pause."""
    return [Node("AXWindow", [Node("AXGroup", [
        Node("AXCheckBox", value=play_value, enabled=play_enabled),
        Node("AXCheckBox", value=0, enabled=pause_enabled)])])]


@pytest.fixture
def stage(engine, monkeypatch):
    """set_playing against a fake clock; open_app is recorded and can reveal windows / start the app."""
    clock = [0.0]
    monkeypatch.setattr(engine, "time", types.SimpleNamespace(
        monotonic=lambda: clock[0], sleep=lambda s: clock.__setitem__(0, clock[0] + s)))
    engine.world.opened = []
    engine.world.clock = clock

    def open_app():
        engine.world.opened.append(1)
        engine.world.on_open()
    monkeypatch.setattr(engine, "open_app", open_app)
    engine.world.on_open = lambda: None
    return engine


def box(windows, index):
    """The index-th checkbox of a controls() window: 0 = Play, 1 = Pause."""
    return windows[0].attrs["AXChildren"][0].attrs["AXChildren"][index]


def test_set_playing_presses_play_when_paused(stage):
    stage.world.windows = controls(0, True, False)
    play = box(stage.world.windows, 0)
    stage.world.on_press = lambda el: setattr(stage.world, "windows", [])  # timing hides the window
    assert stage.set_playing(True) is True
    assert stage.world.pressed == [(play, "AXPress")]
    assert stage.world.opened == []


def test_set_playing_cold_launch_opens_waits_then_presses_play(stage):
    stage.world.apps = []
    launched = controls(0, True, False)

    def launch():
        stage.world.apps = [FakeApp()]
        stage.world.windows = launched
    stage.world.on_open = launch
    stage.world.on_press = lambda el: setattr(stage.world, "windows", [])
    assert stage.set_playing(True) is True
    assert stage.world.opened == [1]
    assert stage.world.pressed == [(box(launched, 0), "AXPress")]


def test_set_playing_pause_reveals_the_hidden_window_and_presses_pause(stage):
    # A timing Monitask hides its window: pausing needs it reopened first.
    stage.world.windows = []
    revealed = controls(0, False, True)
    stage.world.on_open = lambda: setattr(stage.world, "windows", revealed)
    stage.world.on_press = lambda el: setattr(stage.world, "windows", controls(0, True, False))
    assert stage.set_playing(False) is True
    assert stage.world.opened == [1]
    assert stage.world.pressed == [(box(revealed, 1), "AXPress")]


def test_set_playing_without_accessibility_does_not_open_or_press(stage):
    stage.world.trusted = False
    stage.world.windows = controls(0, True, False)
    with pytest.raises(RuntimeError):
        stage.set_playing(True)
    assert stage.world.opened == [] and stage.world.pressed == []
    assert stage.world.prompts == [{"AXTrustedCheckOptionPrompt": True}]  # macOS re-lists the current runner


def test_set_playing_gives_up_without_pressing_when_controls_never_appear(stage):
    stage.world.apps = []
    assert stage.set_playing(True) is False
    assert stage.world.pressed == []
    assert stage.world.opened == [1]  # opened once, not on every poll
    assert stage.world.clock[0] <= stage.LAUNCH_TIMEOUT_S + stage.POLL_S


def test_set_playing_false_when_state_never_changes(stage):
    stage.world.windows = controls(0, True, False)  # press is a no-op
    assert stage.set_playing(True) is False
    assert len(stage.world.pressed) == 1
    assert stage.world.clock[0] <= stage.LAUNCH_TIMEOUT_S + stage.VERIFY_TIMEOUT_S + stage.POLL_S


def test_cmd_check_auto_plays_at_session_start(routed):
    from monitask_center import check
    routed.world.hour, routed.world.state = 10, "paused"
    routed.cmd_check()
    assert routed.presses == [True]
    assert routed.sent == [(routed.t("check.play_ok"), True)]
    assert check.last_check()["action"] == "play"


def test_cmd_check_auto_plays_after_lunch_when_app_closed(routed):
    routed.world.hour, routed.world.state = 13, "not-running"
    routed.cmd_check()
    assert routed.presses == [True]


def test_cmd_check_play_failure_notifies_and_records_failure(routed):
    from monitask_center import check
    routed.world.hour, routed.world.state, routed.world.press_ok = 10, "paused", False
    routed.cmd_check()
    assert routed.sent == [(routed.t("check.play_failed"), True)]
    assert check.last_check()["action"] == "play_failed"


def test_cmd_check_mid_session_paused_only_reminds(routed):
    routed.world.hour, routed.world.state = 14, "paused"
    routed.cmd_check()
    assert routed.presses == []
    assert routed.sent[0][0] == routed.t("check.remind_paused")


@pytest.mark.parametrize("state", ["timing", "hidden"])
def test_cmd_check_pauses_a_running_timer_at_lunch(routed, state):
    from monitask_center import check
    routed.world.hour, routed.world.state = 12, state
    routed.cmd_check()
    assert routed.presses == [False]
    assert routed.sent == [(routed.t("check.pause_ok"), True)]
    assert check.last_check()["action"] == "pause"


def test_cmd_check_pause_failure_notifies(routed):
    from monitask_center import check
    routed.world.hour, routed.world.state, routed.world.press_ok = 12, "timing", False
    routed.cmd_check()
    assert routed.sent == [(routed.t("check.pause_failed"), True)]
    assert check.last_check()["action"] == "pause_failed"


def test_cmd_check_lunch_already_paused_is_silent(routed):
    from monitask_center import check
    routed.world.hour, routed.world.state = 12, "paused"
    routed.cmd_check()
    assert routed.presses == [] and routed.sent == []
    assert check.last_check()["action"] == "skip_lunch"


def test_cmd_check_locked_screen_never_presses(routed):
    routed.world.hour, routed.world.state = 10, "paused"
    routed.world.session = {"kCGSSessionOnConsoleKey": True, "CGSSessionScreenIsLocked": True}
    routed.cmd_check()
    assert routed.presses == [] and routed.sent == []


@pytest.fixture
def overtime(routed, monkeypatch):
    """cmd_overtime with the dialog and the 30-minute wait replaced: answers are consumed in order."""
    answers, waits = [], []
    monkeypatch.setattr(routed, "ask_overtime", lambda: answers.pop(0))
    monkeypatch.setattr(routed.time, "sleep", waits.append)
    routed.answers, routed.waits = answers, waits
    return routed


def test_overtime_is_skipped_during_a_time_range_leave(offline, overtime):
    _leave("17:00~")
    offline.world.hour, offline.world.minute = 18, 30
    assert offline.cmd_overtime() == 0
    assert offline.presses == [] and offline.sent == [] and offline.reads == []


def test_overtime_timing_and_no_answer_pauses(overtime):
    overtime.answers.append("")
    assert overtime.cmd_overtime() == 0
    assert overtime.presses == [False] and overtime.waits == []
    assert len(overtime.sent) == 2  # the notice, then the pause result
    assert overtime.sent[-1] == ("Monitask 已停止計時(下班詢問逾時未回應)", True)


def test_overtime_stop_pauses(overtime):
    overtime.answers.append("stop")
    overtime.cmd_overtime()
    assert overtime.presses == [False]
    assert overtime.sent[-1] == ("Monitask 已停止計時(選擇停止計時)", True)


@pytest.mark.parametrize("telegram", [True, False])
def test_overtime_continue_asks_again_after_30_minutes_then_pauses(overtime, telegram):
    from monitask_center import config
    config.save(config.Settings(telegram=telegram))
    overtime.answers += ["continue", ""]
    overtime.cmd_overtime()
    assert overtime.waits == [1800] and overtime.presses == [False]
    assert len(overtime.sent) == 3  # notice, continue decision, then pause result
    assert overtime.sent[1] == ("已選擇「繼續加班」，未變更 Monitask 計時狀態，30 分鐘後再問", telegram)
    assert overtime.sent[-1] == ("Monitask 已停止計時(下班詢問逾時未回應)", telegram)


@pytest.mark.parametrize("answer,reason", [("stop", "選擇停止計時"), ("", "下班詢問逾時未回應")])
@pytest.mark.parametrize("state", ["paused", "not-running", "unknown"])
def test_overtime_reports_decision_when_timer_changes_during_dialog(overtime, answer, reason, state):
    overtime.answers.append(answer)
    states = iter(["timing", state])
    overtime.read_state = lambda: next(states)
    overtime.cmd_overtime()
    assert overtime.presses == []
    assert len(overtime.sent) == 2
    assert reason in overtime.sent[-1][0]
    assert overtime.state_text(state) in overtime.sent[-1][0]
    assert "未執行自動暫停" in overtime.sent[-1][0]


@pytest.mark.parametrize("state", ["paused", "not-running", "unknown"])
def test_overtime_not_timing_is_silent(overtime, state):
    overtime.world.state = state
    overtime.cmd_overtime()
    assert overtime.presses == [] and overtime.sent == []


def test_overtime_stops_asking_when_paused_by_hand_during_the_wait(overtime):
    overtime.answers += ["continue"]
    states = iter(["timing", "paused"])
    overtime.read_state = lambda: next(states)
    overtime.cmd_overtime()
    assert overtime.presses == []
    assert "未變更 Monitask 計時狀態" in overtime.sent[-1][0]


def test_overtime_pause_failure_is_reported(overtime):
    overtime.world.press_ok = False
    overtime.answers.append("")
    overtime.cmd_overtime()
    assert overtime.presses == [False] and overtime.sent[-1][0] == _t("overtime.pause_failed", why=_t("overtime.why_timeout"))


def test_overtime_stop_outcome_is_logged(overtime, monkeypatch):
    logged = []
    monkeypatch.setattr(overtime.notify, "log", logged.append)
    overtime.world.press_ok = False
    overtime.answers.append("stop")
    overtime.cmd_overtime()
    why = _t("overtime.why_stop")
    assert logged[-2:] == [why, _t("overtime.pause_failed", why=why)]


@pytest.mark.parametrize("setup", ["disabled", "holiday", "locked"])
def test_overtime_skips_without_touching_monitask(overtime, setup):
    from monitask_center import config
    if setup == "disabled":
        config.save(config.Settings(overtime_prompt=False))
    elif setup == "holiday":
        overtime.world.wd = (False, "holiday:weekend")
    else:
        overtime.world.session = {"kCGSSessionOnConsoleKey": True, "CGSSessionScreenIsLocked": True}
    assert overtime.cmd_overtime() == 0
    assert overtime.sent == [] and overtime.reads == [] and overtime.presses == []


def test_overtime_dialog_failure_still_waits_out_the_answer_window(engine, monkeypatch):
    waits = []
    monkeypatch.setattr(engine.subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        returncode=1, stdout="", stderr="boom"))
    monkeypatch.setattr(engine.time, "sleep", waits.append)
    monkeypatch.setattr(engine.notify, "log", lambda msg: None)
    assert engine.ask_overtime() == ""
    assert len(waits) == 1 and 0 < waits[0] <= engine.OVERTIME_ANSWER_S


def test_overtime_dialog_maps_buttons(engine, monkeypatch):
    from monitask_center.i18n import t
    out = {"value": t("overtime.button_stop")}
    monkeypatch.setattr(engine.subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        returncode=0, stdout=out["value"] + "\n", stderr=""))
    assert engine.ask_overtime() == "stop"
    out["value"] = t("overtime.button_continue")
    assert engine.ask_overtime() == "continue"
    out["value"] = ""  # gave up
    assert engine.ask_overtime() == ""


def test_cmd_check_migrates_the_activity_schedule_unattended(routed, monkeypatch):
    logged = []
    monkeypatch.setattr(routed.launchd, "migrate_activity", lambda: "activity moved")
    monkeypatch.setattr(routed.notify, "log", logged.append)
    routed.cmd_check()
    assert "activity moved" in logged
