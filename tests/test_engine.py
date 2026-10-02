import importlib.util
import sys
import types
from pathlib import Path

import pytest

from monitask_center.workday import workday_status as REAL_WORKDAY_STATUS

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "monitask.py"


class Node:
    def __init__(self, role, children=(), value=None, windows=None):
        self.attrs = {"AXRole": role, "AXChildren": list(children), "AXValue": value}
        if windows is not None:
            self.attrs["AXWindows"] = windows


class FakeApp:
    def localizedName(self):
        return "Monitask"

    def processIdentifier(self):
        return 1


@pytest.fixture
def engine(monkeypatch):
    """monitask.py imported against fake pyobjc modules (pyobjc is not installable in the uv env)."""
    world = types.SimpleNamespace(apps=[FakeApp()], trusted=True, windows=[], session={
        "kCGSSessionOnConsoleKey": True, "CGSSessionScreenIsLocked": False})
    appkit = types.ModuleType("AppKit")
    appkit.NSWorkspace = types.SimpleNamespace(sharedWorkspace=lambda: types.SimpleNamespace(
        runningApplications=lambda: world.apps))
    ax = types.ModuleType("ApplicationServices")
    ax.AXIsProcessTrusted = lambda: world.trusted
    ax.AXUIElementCreateApplication = lambda pid: Node("AXApplication", windows=world.windows)
    ax.AXUIElementSetMessagingTimeout = lambda el, s: 0
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


def window(*values):
    return Node("AXWindow", [Node("AXGroup", [Node("AXCheckBox", value=v) for v in values])])


def test_source_has_no_press_or_dock_click():
    src = ENGINE.read_text(encoding="utf-8")
    assert "AXPress" not in src and "press_play_pause" not in src
    assert "open -a" not in src and '"Dock"' not in src


def test_read_state_not_running(engine):
    engine.world.apps = []
    assert engine.read_state() == "not-running"


def test_read_state_untrusted_is_unknown(engine):
    engine.world.trusted = False
    engine.world.windows = [window(1, 0)]
    assert engine.read_state() == "unknown"


def test_read_state_hidden_when_no_windows(engine):
    assert engine.read_state() == "hidden"


def test_read_state_too_few_checkboxes_is_unknown(engine):
    engine.world.windows = [window(1)]
    assert engine.read_state() == "unknown"


@pytest.mark.parametrize("first,expected", [(1, "timing"), (0, "paused")])
def test_read_state_first_checkbox_decides(engine, first, expected):
    engine.world.windows = [window(first, 1 - first)]
    assert engine.read_state() == expected


def test_read_state_ax_error_is_unknown(engine, monkeypatch):
    def boom(app):
        raise RuntimeError("ax disabled")
    monkeypatch.setattr(engine, "windows", boom)
    assert engine.read_state() == "unknown"


@pytest.fixture
def routed(engine, monkeypatch):
    """cmd_check with the clock, calendar and notifier replaced by recorders."""
    sent = []
    reads = []
    monkeypatch.setattr(engine.notify, "notify", lambda msg, tg=True: sent.append((msg, tg)))
    monkeypatch.setattr(engine.notify, "log", lambda msg: None)
    monkeypatch.setattr(engine.workday, "workday_status", lambda d, s: engine.world.wd)
    engine.world.wd = (True, "workday")
    engine.world.state = "timing"
    engine.world.hour = 14

    def fake_read():
        reads.append(1)
        return engine.world.state
    monkeypatch.setattr(engine, "read_state", fake_read)

    class Clock:
        @staticmethod
        def now():
            return types.SimpleNamespace(hour=engine.world.hour)
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
    ((True, "workday"), 12, "skip_lunch"),
    ((True, "workday"), 8, "outside"),
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
    engine.world.windows = [window(1, 0)]
    engine.cmd_status()
    out = dict(line.split("=") for line in capsys.readouterr().out.split())
    assert out == {"accessibility_trusted": "True", "running": "True",
                   "state": "timing", "gui_session": "True"}


def test_last_error_uses_catalog_keys(engine, monkeypatch):
    engine.world.windows = [window(1)]
    assert engine.read_state() == "unknown"
    assert engine._last_error == engine.t("check.err.checkboxes", count=1) != "check.err.checkboxes"
    assert "checkboxes=" not in engine._last_error

    def boom(app):
        raise RuntimeError("ax disabled")
    monkeypatch.setattr(engine, "windows", boom)
    engine.read_state()
    assert engine._last_error == engine.t("check.err.exception", error="ax disabled")


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


@pytest.mark.parametrize("hour,action", [(12, "skip_lunch"), (9, "outside"), (19, "outside")])
def test_cmd_check_gates_run_before_calendar(offline, hour, action):
    from monitask_center import check
    offline.world.hour = hour
    assert offline.cmd_check() == 0
    assert offline.sent == [] and offline.downloads == [] and offline.reads == []
    assert check.last_check()["action"] == action


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
