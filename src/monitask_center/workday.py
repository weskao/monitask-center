"""Taiwan workday check: government calendar + personal vacation (stdlib only).

Calendar: https://cdn.jsdelivr.net/gh/ruyut/TaiwanCalendar@latest/data/<YEAR>.json,
one record per day; isHoliday=false on a Saturday is a make-up workday.
Cached forever per year under ~/.monitask-center/holidays/. Any failure is
fail-open (treated as a workday) with one warning per day.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date
from functools import cache
from pathlib import Path

from . import config
from .i18n import t
from .notify import log, notify

CALENDAR_URL = "https://cdn.jsdelivr.net/gh/ruyut/TaiwanCalendar@latest/data/{year}.json"
DOWNLOAD_TIMEOUT = 8
MAX_PAYLOAD = 2 * 1024 * 1024


def holiday_dir() -> Path:
    return config.home_dir() / "holidays"


def decide(d: date, records, vacation: set[date]) -> tuple[bool, str]:
    """Pure decision: (is workday, reason for the log)."""
    if d in vacation:
        return False, "vacation"
    if not records:
        return True, "calendar-unavailable(fail-open)"
    key = d.strftime("%Y%m%d")
    record = next((r for r in records if isinstance(r, dict) and r.get("date") == key), None)
    if record is None:
        return True, "date-not-in-calendar(fail-open)"
    if record.get("isHoliday"):
        return False, "holiday:" + (record.get("description") or "weekend")
    return True, "workday" + (f":{record['description']}" if record.get("description") else "")


def _warn_once(message: str, use_telegram: bool) -> None:
    """At most one notification per day (marker file); the caller already logged it."""
    marker = config.data_dir() / f"{date.today():%Y-%m-%d}-calendar-warning.ok"
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        os.close(os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644))
    except OSError:  # FileExistsError included: already warned today
        return
    notify(t("workday.fail_open_notice", detail=message), use_telegram)


def _download(year: int, dest: Path) -> list:
    import urllib.request  # only paid on the first run of a year

    with urllib.request.urlopen(CALENDAR_URL.format(year=year), timeout=DOWNLOAD_TIMEOUT) as resp:
        payload = resp.read(MAX_PAYLOAD + 1)
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(t("workday.err.too_large"))
    records = json.loads(payload.decode("utf-8"))
    if not isinstance(records, list) or not records:
        raise ValueError(t("workday.err.bad_format"))
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=f".{year}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        os.chmod(tmp, 0o644)
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return records


@cache
def _fetch(year: int) -> tuple[list | None, str | None]:
    """(records, failure message). Cached per year for the process, failures included
    (a failed download is never retried, so toggling Telegram cannot re-block)."""
    cached = holiday_dir() / f"{year}.json"
    if cached.is_file():
        try:
            records = json.loads(cached.read_text(encoding="utf-8"))
            if not isinstance(records, list) or not records:
                raise ValueError(t("workday.err.empty"))
            return records, None
        except (OSError, ValueError) as exc:
            message = t("workday.cache_broken", path=cached, error=exc)
            log(message)
            return None, message
    try:
        records = _download(year, cached)
    except Exception as exc:  # noqa: BLE001 - every download failure is fail-open
        message = t("workday.download_failed", year=year, error=exc)
        log(message)
        return None, message
    log(t("workday.downloaded", year=year, path=cached, count=len(records)))
    return records, None


def load_calendar(year: int, use_telegram: bool = True, send: bool = True):
    """Records or None (fail-open). send=False: failures are logged only, never notified."""
    records, failure = _fetch(year)
    if failure and send:
        _warn_once(failure, use_telegram)
    return records


load_calendar.cache_clear = _fetch.cache_clear  # type: ignore[attr-defined]


def workday_status(d: date, settings: config.Settings, notify: bool = True) -> tuple[bool, str]:
    """Never raises: unexpected errors are fail-open. notify=False for read-only views."""
    try:
        vacation = {v for v in map(config.parse_date, settings.vacation) if v}
        if d in vacation:  # vacation beats the calendar: no download, no warning
            return decide(d, None, vacation)
        extra = {} if notify else {"send": False}
        return decide(d, load_calendar(d.year, settings.telegram, **extra), vacation)
    except Exception as exc:  # noqa: BLE001
        log(t("workday.unexpected_error", error=exc))
        return True, "error(fail-open)"


def peek_status(d: date, settings: config.Settings) -> tuple[bool, str]:
    return workday_status(d, settings, notify=False)
