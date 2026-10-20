import json
from datetime import date

import pytest

from monitask_center import notify, workday
from monitask_center.config import Settings

REC = [
    {"date": "20261001", "isHoliday": False, "description": ""},
    {"date": "20261010", "isHoliday": True, "description": "National Day"},
    {"date": "20261011", "isHoliday": True, "description": ""},
    {"date": "20261017", "isHoliday": False, "description": "make-up"},
]


def test_decide():
    d = workday.decide
    assert d(date(2026, 10, 1), REC, set()) == (True, "workday")
    assert d(date(2026, 10, 10), REC, set()) == (False, "holiday:National Day")
    assert d(date(2026, 10, 11), REC, set()) == (False, "holiday:weekend")
    assert d(date(2026, 10, 17), REC, set()) == (True, "workday:make-up")
    assert d(date(2026, 10, 1), REC, {date(2026, 10, 1)}) == (False, "vacation")  # vacation beats calendar
    assert d(date(2026, 10, 10), None, {date(2026, 10, 10)}) == (False, "vacation")
    assert d(date(2026, 10, 1), None, set())[0] is True
    assert d(date(2026, 10, 2), REC, set())[0] is True  # date missing: fail-open


def _cache(year, records):
    path = workday.holiday_dir() / f"{year}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(records))


def test_status_uses_cache_and_vacation(monkeypatch):
    _cache(2026, REC)
    monkeypatch.setattr(workday, "_download", lambda *a: (_ for _ in ()).throw(AssertionError("no network")))
    assert workday.workday_status(date(2026, 10, 10), Settings()) == (False, "holiday:National Day")
    assert workday.workday_status(date(2026, 10, 1), Settings(vacation=("2026-10-01",))) == (False, "vacation")


def test_download_failure_is_fail_open_and_warns_once(monkeypatch):
    sent = []
    monkeypatch.setattr(workday, "notify", lambda msg, tg=True: sent.append(msg))
    monkeypatch.setattr(notify, "log", lambda m: None)
    monkeypatch.setattr(workday, "log", lambda m: None)

    def boom(year, dest):
        raise OSError("offline")

    monkeypatch.setattr(workday, "_download", boom)
    assert workday.workday_status(date(2026, 10, 10), Settings())[0] is True
    workday.load_calendar.cache_clear()
    assert workday.workday_status(date(2026, 10, 10), Settings())[0] is True
    assert len(sent) == 1 and "offline" in sent[0]  # one warning per day
    assert "{detail}" not in sent[0] and "workday." not in sent[0]  # catalog key resolved


def test_broken_cache_is_fail_open(monkeypatch):
    path = workday.holiday_dir() / "2026.json"
    path.parent.mkdir(parents=True)
    path.write_text("{oops")
    monkeypatch.setattr(workday, "notify", lambda *a: None)
    monkeypatch.setattr(workday, "log", lambda m: None)
    assert workday.workday_status(date(2026, 10, 10), Settings())[0] is True


def test_unexpected_error_is_fail_open(monkeypatch):
    def boom(*a):
        raise RuntimeError("x")

    monkeypatch.setattr(workday, "load_calendar", boom)
    monkeypatch.setattr(workday, "log", lambda m: None)
    assert workday.workday_status(date(2026, 10, 1), Settings()) == (True, "error(fail-open)")


def _no_log(monkeypatch):
    monkeypatch.setattr(workday, "log", lambda m: None)


def test_vacation_beats_calendar_without_download(monkeypatch):
    sent = []
    monkeypatch.setattr(workday, "notify", lambda *a: sent.append(a))
    monkeypatch.setattr(workday, "_download", lambda *a: (_ for _ in ()).throw(AssertionError("downloaded")))
    s = Settings(vacation=("2026-10-01", "2026-10-02", "2026-10-03"))
    assert workday.workday_status(date(2026, 10, 2), s) == (False, "vacation")
    assert sent == []  # no calendar load, so no fail-open warning either


def test_notify_false_logs_but_sends_nothing(monkeypatch):
    sent, logged = [], []
    monkeypatch.setattr(workday, "notify", lambda *a: sent.append(a))
    monkeypatch.setattr(workday, "log", logged.append)
    monkeypatch.setattr(workday, "_download", lambda *a: (_ for _ in ()).throw(OSError("offline")))
    assert workday.peek_status(date(2026, 10, 10), Settings())[0] is True
    assert sent == [] and len(logged) == 1 and "offline" in logged[0]
    assert not list(workday.config.data_dir().glob("*calendar-warning.ok"))  # marker untouched
    assert workday.workday_status(date(2026, 10, 10), Settings())[0] is True  # real check still warns
    assert len(sent) == 1


def test_failed_download_is_negative_cached_across_telegram_toggle(monkeypatch):
    _no_log(monkeypatch)
    monkeypatch.setattr(workday, "notify", lambda *a: None)
    calls = []

    def boom(year, dest):
        calls.append(year)
        raise OSError("offline")

    monkeypatch.setattr(workday, "_download", boom)
    for tg in (True, False, True):
        workday.workday_status(date(2026, 10, 10), Settings(telegram=tg))
        workday.peek_status(date(2026, 10, 10), Settings(telegram=tg))
    assert calls == [2026]


def test_calendar_cached_once_per_year_across_telegram_toggle(monkeypatch):
    _cache(2026, REC)
    workday.load_calendar.cache_clear()
    reads = []
    real = json.loads
    monkeypatch.setattr(workday.json, "loads", lambda *a, **k: (reads.append(1), real(*a, **k))[1])
    for tg in (True, False):
        assert workday.workday_status(date(2026, 10, 10), Settings(telegram=tg))[0] is False
    assert len(reads) == 1


def test_error_texts_come_from_catalog(monkeypatch):
    import io
    import urllib.request

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

    for body, key in ((b"x" * (workday.MAX_PAYLOAD + 1), "too_large"), (b"{}", "bad_format")):
        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, _b=body, **k: Resp(_b))
        with pytest.raises(ValueError) as e:
            workday._download(2026, workday.holiday_dir() / "2026.json")
        assert "workday.err" not in str(e.value) and str(e.value) != key
