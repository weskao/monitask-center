import json
import os
import subprocess
import time
from datetime import datetime

import pytest

from monitask_center import check, config, launchd, status, workday
from monitask_center.config import Settings
from monitask_center.i18n import t

NOW = datetime(2026, 10, 2, 9, 30)  # a Friday
CALENDAR = [  # Fri-Sun workday/holiday records, then Mon..Wed workdays
    {"date": "20261002", "isHoliday": False, "description": ""},
    {"date": "20261003", "isHoliday": True, "description": ""},
    {"date": "20261004", "isHoliday": True, "description": ""},
    *({"date": f"2026100{d}", "isHoliday": False, "description": ""} for d in (5, 6, 7)),
]
REAL_LOAD_CALENDAR = workday.load_calendar
RUNNER_OUT = "accessibility_trusted=True\nrunning=True\nstate=timing\ngui_session=True\n"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(workday, "load_calendar", lambda year, use_telegram=True, send=True: CALENDAR)
    monkeypatch.setattr(launchd, "loaded", lambda: [])
    set_runner(monkeypatch, 0, RUNNER_OUT)


def set_runner(monkeypatch, code, out):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, code, out, ""))


def rows(settings=None, now=NOW):
    return {r.key: r for r in status.snapshot(settings or Settings(), now)}


def test_basic_snapshot():
    r = rows(Settings(vacation=("2026-09-01", "2026-10-05", "2026-10-06")))
    assert r["workday"].level == "ok" and t("status.workday.yes") in r["workday"].value
    assert t("status.hours.lunch_mark", hour=12) in r["hours"].value
    assert t("status.hours.plain", hour=13) in r["hours"].value and "18:00" in r["hours"].value
    assert r["next"].value == "2026-10-02 10:00"
    assert r["lunch"].value == t("status.lunch.window", start=12, end=13)
    assert "2026-10-05" in r["vacation"].value and "2026-09-01" not in r["vacation"].value
    assert (r["launchd"].value, r["launchd"].level) == (t("status.launchd.not_installed"), "warn")
    assert (r["monitask"].value, r["monitask"].level) == (t("status.state.timing"), "ok")
    assert r["accessibility"].level == "ok"
    assert r["last_check"].value == t("status.last_check.none")
    assert all(row.label != row.key and row.label for row in r.values())  # labels are translated


def test_next_check_skips_lunch_and_after_hours_and_off_days():
    assert rows(now=datetime(2026, 10, 2, 11, 30))["next"].value == "2026-10-02 13:00"
    # after 18:00 on Friday -> weekend holidays skipped -> Monday 10:00
    assert rows(now=datetime(2026, 10, 2, 18, 30))["next"].value == "2026-10-05 10:00"
    # vacation Mon+Tue pushes it to Wednesday
    s = Settings(vacation=("2026-10-05", "2026-10-06"))
    assert rows(s, datetime(2026, 10, 2, 18, 30))["next"].value == "2026-10-07 10:00"
    # lunch skip off: 12:00 is a real check
    assert rows(Settings(lunch_skip=False), datetime(2026, 10, 2, 11, 30))["next"].value == "2026-10-02 12:00"
    assert rows(Settings(lunch_skip=False))["lunch"].value == t("status.lunch.off")


def test_vacation_today_is_not_a_workday_and_lists_overflow():
    days = tuple(f"2026-10-{d:02d}" for d in range(2, 12))
    r = rows(Settings(vacation=days))
    assert r["workday"].level == "info" and t("status.workday.no") in r["workday"].value
    assert t("status.vacation.more", count=len(days) - status.MAX_VACATION_SHOWN) in r["vacation"].value


@pytest.mark.parametrize("code,out", [(1, "dyld: Library not loaded"), (0, ""), (1, "state=timing")])
def test_runner_not_working_is_unknown(monkeypatch, code, out):
    set_runner(monkeypatch, code, out)
    r = rows()
    for key in ("monitask", "accessibility"):
        assert (r[key].value, r[key].level) == (t("status.unknown"), "warn")


def test_runner_missing_oserror(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("bash")

    monkeypatch.setattr(subprocess, "run", boom)
    assert rows()["monitask"].value == t("status.unknown")


def test_paused_and_untrusted_warn(monkeypatch):
    set_runner(monkeypatch, 0, "accessibility_trusted=False\nrunning=True\nstate=paused\n")
    r = rows()
    assert (r["monitask"].value, r["monitask"].level) == (t("status.state.paused"), "warn")
    assert r["accessibility"].level == "warn"


def test_launchd_rows(monkeypatch):
    monkeypatch.setattr(launchd, "_launchctl", lambda *a, check=False: None)
    launchd.install(Settings())
    assert rows()["launchd"].value == t("status.launchd.not_loaded")
    monkeypatch.setattr(launchd, "loaded", lambda: ["-\t0\tcom.monitask-center.check"])
    assert (rows()["launchd"].value, rows()["launchd"].level) == (t("status.launchd.loaded"), "ok")
    assert rows(Settings(start_hour=9))["launchd"].value == t("status.launchd.stale")


def test_last_check_and_lines():
    check.record_last_check("remind", "paused", NOW)
    value = rows()["last_check"].value
    assert "2026-10-02T09:30:00" in value
    assert t("status.action.remind") in value and t("status.state.paused") in value
    assert "remind" not in value and "paused" not in value  # no raw internal codes
    lines = status.status_lines(Settings(), NOW)
    assert all(isinstance(line, str) for line in lines) and len(lines) == 9
    assert lines[0].startswith(t("status.label.monitask"))
    json.loads(check.last_check_path().read_text())  # still valid after a read-only snapshot


def test_row_order_puts_important_rows_first_and_settings_echo_last():
    keys = list(rows())
    assert keys == ["monitask", "last_check", "next", "workday", "launchd", "accessibility",
                    "hours", "lunch", "vacation"]


@pytest.mark.parametrize("reason,expected", [
    ("workday", t("status.reason.workday")),
    ("workday:" + "\u88dc\u73ed", t("status.reason.workday_desc", desc="\u88dc\u73ed")),
    ("holiday:" + "\u5e73\u5b89\u65e5", t("status.reason.holiday", desc="\u5e73\u5b89\u65e5")),
    ("holiday:weekend", t("status.reason.weekend")),
    ("vacation", t("status.reason.vacation")),
    ("calendar-unavailable(fail-open)", t("status.reason.calendar-unavailable(fail-open)")),
    ("date-not-in-calendar(fail-open)", t("status.reason.date-not-in-calendar(fail-open)")),
    ("error(fail-open)", t("status.reason.error(fail-open)")),
])
def test_reason_text_is_translated_and_keeps_calendar_names(reason, expected):
    out = status.reason_text(reason)
    assert out == expected
    assert "fail-open" not in out and not out.startswith(("status.", "holiday:", "workday:", "vacation"))


def test_every_state_and_action_has_text():
    for state in check.STATES:
        assert status.state_text(state) != state or state == "unknown"
        assert not status.state_text(state).startswith("status.")
    assert status.state_text("n/a") == t("status.state.n/a")
    assert status.state_text("something-new") == t("status.state.unknown")
    for action in ("ok", "remind", "warn_unknown", "skip_vacation", "skip_holiday", "skip_lunch",
                   "outside", "locked"):
        assert not status.action_text(action).startswith("status.") and status.action_text(action) != action


def test_workday_row_shows_translated_reason():
    r = rows(Settings(vacation=("2026-10-02",)))
    assert t("status.reason.vacation") in r["workday"].value and "vacation" not in r["workday"].value
    assert status.workday_line(False, "holiday:weekend") == t(
        "status.workday.line", verdict=t("status.workday.no"), reason=t("status.reason.weekend"))


@pytest.fixture
def failing_calendar(monkeypatch):
    """Calendar download fails: workday_status(notify=True) would send a fail-open notice."""
    sent = []
    monkeypatch.setattr(workday, "load_calendar", REAL_LOAD_CALENDAR)  # undo the autouse offline stub
    monkeypatch.setattr(workday, "_fetch", lambda year: (None, "boom"))
    monkeypatch.setattr(workday, "notify", lambda *a, **k: sent.append(a))
    return sent


def test_snapshot_and_status_lines_never_notify(failing_calendar):
    assert workday.workday_status(NOW.date(), Settings())[0] and len(failing_calendar) == 1  # sanity: real path notifies
    failing_calendar.clear()
    for marker in config.data_dir().glob("*-calendar-warning.ok"):  # re-arm the once-a-day notice
        marker.unlink()
    r = rows()
    assert r["workday"].level == "ok" and t("status.reason.calendar-unavailable(fail-open)") in r["workday"].value
    assert r["next"].value
    status.status_lines(Settings(), NOW)
    assert failing_calendar == []


def write_activity(date, daily, time="09:20"):
    path = config.home_dir() / status.ACTIVITY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"date": date, "time": time, "daily": daily}), encoding="utf-8")


@pytest.mark.parametrize(("daily", "level"), [("59.9", "error"), ("64.45", "warn"), ("80", "ok")])
def test_activity_row_shows_todays_rate_with_its_tone(daily, level):
    write_activity("2026-10-02", daily)
    r = rows()["activity"]
    assert (r.mark, r.level) == (f"{daily}%", level)
    assert r.value == t("status.activity.value", rate=f"{daily}%", time="09:20")
    line = status.status_lines(Settings(), NOW)[1]
    assert line == (f"{t('status.label.activity')}: {r.value}", f"{daily}%", level)


@pytest.mark.parametrize("content", [None, '{"date": "2026-10-01", "daily": "70"}', "not json", '{"date": "2026-10-02"}'])
def test_no_activity_row_without_a_rate_for_today(content):
    if content is not None:
        path = config.home_dir() / status.ACTIVITY_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    assert "activity" not in rows()
    assert all(isinstance(x, str) for x in status.status_lines(Settings(), NOW))


def test_a_running_fetch_shows_a_spinner_until_todays_rate_exists():
    r = status.snapshot(Settings(), NOW, fetching=True)
    spin = next(x for x in r if x.key == "activity")
    assert (spin.level, spin.mark) == ("spin", status.ACTIVITY_SPIN)
    assert spin.value.startswith(status.ACTIVITY_SPIN) and t("status.activity.loading") in spin.value
    write_activity("2026-10-02", "70")
    assert rows()["activity"].level == "warn"
    assert status.snapshot(Settings(), NOW, fetching=True)[1].level == "warn"  # a recorded rate wins


def _install_activity_job():
    script = launchd.activity_runner()
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("#!/bin/bash\n")
    launchd.agents_dir().mkdir(parents=True, exist_ok=True)
    launchd.plist_path("activity").write_bytes(
        __import__("plistlib").dumps({"ProgramArguments": ["/bin/bash", str(script), "fetch"]}))
    return script


def test_start_activity_fetch_runs_record_in_its_own_session_only_when_needed(monkeypatch):
    started = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: started.append((cmd, kw)) or "proc")
    assert status.start_activity_fetch(NOW) is None and not started  # job not installed
    script = _install_activity_job()
    assert status.start_activity_fetch(NOW) == "proc"
    cmd, kw = started[0]
    assert cmd == ["/bin/bash", str(script), "record"] and kw["start_new_session"]
    write_activity("2026-10-02", "70")
    assert status.start_activity_fetch(NOW) is None and len(started) == 1  # today's fresh rate already there
    stale = time.time() - status.ACTIVITY_FRESH_SECONDS - 1
    os.utime(config.home_dir() / status.ACTIVITY_FILE, (stale, stale))
    assert status.start_activity_fetch(NOW) == "proc" and len(started) == 2  # a stale rate is refetched
    assert rows()["activity"].mark == "70%"  # and stays shown meanwhile
