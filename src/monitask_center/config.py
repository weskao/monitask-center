"""User settings: one JSON file owned by the tool (~/.monitask-center/config.json).

No secret is ever written here. The Telegram bot token is filed in the OS credential
store instead -- see :mod:`monitask_center.telegram` -- and this module holds only the
non-secret chat id that goes with it.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, fields, replace
from datetime import date, datetime, timedelta
from pathlib import Path

from . import i18n

DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d")
MAX_RANGE_DAYS = 366
# A time-range leave: "<date> 14:00~16:00", or "<date> 15:30~" for the rest of the day.
LEAVE_RE = re.compile(r"(\S+)\s+(\d{1,2}):(\d{2})\s*[~-]\s*(?:(\d{1,2}):(\d{2}))?")
DAY_END = 24 * 60
# Overtime prompt time, in minutes after midnight: 18:00 to 20:30 in half-hour steps.
OVERTIME_EARLIEST = 18 * 60
OVERTIME_LATEST = 20 * 60 + 30
OVERTIME_STEP = 30
# Low activity alert: threshold (%) and check frequency (minutes) are picked from these, never typed.
# Frequencies are whole tens and counted from the start hour, so checks land on the activity job's
# 10-minute ticks.
ALERT_PERCENTS = tuple(range(10, 95, 5))
ALERT_MINUTES = tuple(range(10, 61, 10))


def home_dir() -> Path:
    return Path(os.environ.get("MONITASK_CENTER_HOME") or Path.home() / ".monitask-center")


def config_path() -> Path:
    return home_dir() / "config.json"


def data_dir() -> Path:
    """The launchd runtime (run.sh, MonitaskCenter.app, pylibs), logs, lock and launchd output."""
    return Path.home() / "Library/Application Support/MonitaskCenter"


#: launchd entry point, written by install (see install.render_runner). Lives outside any checkout,
#: so a `uv tool install --from git+...@vX.Y.Z` install needs no clone.
RUNNER = data_dir() / "run.sh"


@dataclass(frozen=True)
class Settings:
    language: str = i18n.DEFAULT_LANGUAGE
    start_hour: int = 10
    end_hour: int = 18
    interval_hours: int = 1
    auto_play_pause: bool = True     # False = never press Play/Pause in Monitask; notify only
    lunch_skip: bool = True          # checks inside the lunch window stay silent
    lunch_start_hour: int = 12
    lunch_end_hour: int = 13         # exclusive: a check at this hour is a normal one
    overtime_prompt: bool = True     # workdays: still timing at overtime_minutes -> ask to stop
    overtime_minutes: int = 18 * 60 + 30  # minutes after midnight (18:30)
    telegram: bool = True            # False = macOS notifications only
    activity_alert: bool = True      # False = the activity job never compares; the two below are kept
    activity_alert_percent: int = 60  # today's activity below this -> Telegram alert (activity job)
    activity_check_minutes: int = 10  # how often the activity job compares it, in work hours
    update_check: bool = True        # background check for a newer release, offered after a command
    telegram_chat_id: str = ""       # an identifier, not a secret: stored here in plain JSON.
                                     # The bot token is NOT a field -- it lives in the OS
                                     # credential store, see monitask_center.telegram
    vacation: tuple[str, ...] = ()   # ISO dates, or time ranges (see LEAVE_RE), with no checks and no reminders

    def check_hours(self) -> list[int]:
        return [*range(self.start_hour, self.end_hour, self.interval_hours), self.end_hour]

    def play_hours(self) -> set[int]:
        """Ticks that auto-press Play: the first check, and the one when lunch ends."""
        return {self.start_hour} | ({self.lunch_end_hour} if self.lunch_skip else set())

    def is_lunch(self, hour: int) -> bool:
        """Runtime decision only: lunch never changes the launchd schedule."""
        return self.lunch_skip and self.lunch_start_hour <= hour < self.lunch_end_hour

    def is_leave(self, d: date, minute: int) -> bool:
        """Vacation at *minute* after midnight of *d*: a whole day, or inside a time range (end exclusive)."""
        day = d.isoformat()
        return any(e == day or ((leave := parse_leave(e)) is not None and leave[1] <= minute < leave[2])
                   for e in self.vacation if e.startswith(day))


def max_interval(start: int, end: int) -> int:
    return max(1, end - start)


def valid_overtime(minutes: int) -> bool:
    return OVERTIME_EARLIEST <= minutes <= OVERTIME_LATEST and minutes % OVERTIME_STEP == 0


def clock(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def validate(s: Settings) -> Settings:
    """Normalize language/vacation; raise ValueError for an impossible window."""
    if not 0 <= s.start_hour < s.end_hour <= 23:
        raise ValueError("hours")
    if not 1 <= s.interval_hours <= max_interval(s.start_hour, s.end_hour):
        raise ValueError("interval")
    if not 0 <= s.lunch_start_hour < s.lunch_end_hour <= 24:
        raise ValueError("lunch")
    if not valid_overtime(s.overtime_minutes):
        raise ValueError("overtime")
    if s.activity_alert_percent not in ALERT_PERCENTS or s.activity_check_minutes not in ALERT_MINUTES:
        raise ValueError("activity_alert")
    language = s.language if s.language in i18n.available() else i18n.DEFAULT_LANGUAGE
    days = sorted({e for e in map(vacation_entry, s.vacation) if e})
    return replace(s, language=language, vacation=tuple(days))


_reported: set[tuple[str, str]] = set()  # (path, reason) already logged by this process


def _report_corrupt(path: Path, reason: str) -> None:
    if (str(path), reason) in _reported:
        return
    _reported.add((str(path), reason))
    from .notify import log  # lazy: notify imports this module
    log(i18n.t("config.corrupt", path=path, reason=reason))


_backups: dict[str, Path] = {}  # config path -> the .corrupt copy made or found by load()


def corrupt_backup(path: Path | None = None) -> Path | None:
    """The backup of a corrupt config seen by load() in this process, else None."""
    return _backups.get(str(path or config_path()))


def _backup_corrupt(path: Path) -> None:
    """Copy a corrupt config to config.json.corrupt (then .corrupt.1, .2 ...) before a save can
    replace it. Never overwrites a backup; an identical existing one is reused, not duplicated."""
    try:
        data = path.read_bytes() if path.is_file() else None
        if data is None:
            return
        for n in range(1000):
            dest = path.with_name(f"{path.name}.corrupt" + (f".{n}" if n else ""))
            try:
                with open(dest, "xb") as fh:
                    fh.write(data)
            except FileExistsError:
                if dest.read_bytes() == data:
                    _backups[str(path)] = dest
                    return
                continue
            _backups[str(path)] = dest
            from .notify import log  # lazy: notify imports this module
            log(i18n.t("config.corrupt_saved", path=dest))
            return
    except OSError:
        pass  # best effort: an unwritable directory must not break loading


def load(path: Path | None = None) -> Settings:
    path = path or config_path()
    base = Settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return base
    except (OSError, ValueError) as exc:
        _report_corrupt(path, type(exc).__name__)
        _backup_corrupt(path)
        return base
    if not isinstance(raw, dict):
        _report_corrupt(path, type(raw).__name__)
        _backup_corrupt(path)
        return base
    values = {}
    for f in fields(Settings):
        default = getattr(base, f.name)
        value = raw.get(f.name, default)
        if f.name == "vacation":  # keep the valid entries; validate() drops unparseable dates
            value = tuple(v for v in value if isinstance(v, str)) if isinstance(
                value, (list, tuple)) else default
        elif type(value) is not type(default):  # bool is not int here, on purpose
            value = default
        values[f.name] = value
    settings = Settings(**values)
    if 0 <= settings.start_hour < settings.end_hour <= 23:  # clamp, never reset a good window
        span = max_interval(settings.start_hour, settings.end_hour)
        settings = replace(settings, interval_hours=min(max(settings.interval_hours, 1), span))
    else:
        settings = replace(settings, start_hour=base.start_hour, end_hour=base.end_hour,
                           interval_hours=base.interval_hours)
    if not 0 <= settings.lunch_start_hour < settings.lunch_end_hour <= 24:
        settings = replace(settings, lunch_start_hour=base.lunch_start_hour,
                           lunch_end_hour=base.lunch_end_hour)
    if not valid_overtime(settings.overtime_minutes):
        settings = replace(settings, overtime_minutes=base.overtime_minutes)
    if settings.activity_alert_percent not in ALERT_PERCENTS:
        settings = replace(settings, activity_alert_percent=base.activity_alert_percent)
    if settings.activity_check_minutes not in ALERT_MINUTES:
        settings = replace(settings, activity_check_minutes=base.activity_check_minutes)
    return validate(settings)


def save(settings: Settings, path: Path | None = None) -> None:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(validate(settings))
    data["vacation"] = list(data["vacation"])
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def parse_date(token: str) -> date | None:
    token = token.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(token, fmt).date()
        except ValueError:
            continue
    return None


def parse_dates(text: str) -> list[date]:
    """One date, or an inclusive range "2026-10-05~2026-10-09". Raises ValueError."""
    parts = [p for p in text.replace("..", "~").split("~")]
    if len(parts) == 1:
        day = parse_date(parts[0])
        if day is None:
            raise ValueError("date")
        return [day]
    if len(parts) != 2:
        raise ValueError("date")
    first, last = parse_date(parts[0]), parse_date(parts[1])
    if first is None or last is None or last < first:
        raise ValueError("date")
    if (last - first).days >= MAX_RANGE_DAYS:
        raise ValueError("range")
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def _minutes(hour: str, minute: str) -> int | None:
    h, m = int(hour), int(minute)
    return h * 60 + m if h < 24 and m < 60 else None


def parse_leave(token: str) -> tuple[date, int, int] | None:
    """A time range -> (day, start, end) in minutes after midnight, end exclusive; None when invalid."""
    match = LEAVE_RE.fullmatch(token.strip())
    if not match:
        return None
    day, start = parse_date(match[1]), _minutes(match[2], match[3])
    end = _minutes(match[4], match[5]) if match[4] else DAY_END
    if day is None or start is None or end is None or start >= end:
        return None
    return day, start, end


def leave_text(day: date, start: int, end: int) -> str:
    return f"{day.isoformat()} {clock(start)}~{'' if end == DAY_END else clock(end)}"


def vacation_entry(token: str) -> str | None:
    """A vacation entry in its stored form (ISO day or canonical time range); None when invalid."""
    if ":" in token:
        leave = parse_leave(token)
        return leave_text(*leave) if leave else None
    day = parse_date(token)
    return day.isoformat() if day else None


def parse_vacation(text: str) -> list[str]:
    """parse_dates, or one time range like "2026-11-05 14:00~16:00" -> stored entries. Raises ValueError."""
    if ":" not in text:
        return [d.isoformat() for d in parse_dates(text)]
    entry = vacation_entry(text)
    if entry is None:
        raise ValueError("time")
    return [entry]
