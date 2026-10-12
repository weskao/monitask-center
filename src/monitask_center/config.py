"""User settings: one JSON file owned by the tool (~/.monitask-center/config.json).

No secret is ever written here. The Telegram bot token is filed in the OS credential
store instead -- see :mod:`monitask_center.telegram` -- and this module holds only the
non-secret chat id that goes with it.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from dataclasses import asdict, dataclass, fields, replace
from datetime import date, datetime, timedelta
from pathlib import Path

from . import i18n


def project_root(package_root: Path, prefix: Path) -> Path:
    """The checkout holding run.sh and MonitaskCenter.app that launchd needs.

    An editable install (uv tool install --editable .) keeps the package inside it. A plain
    `uv tool install <dir>` copies the package into the tool venv instead, so fall back to the
    source directory uv recorded in the venv's receipt."""
    if (package_root / "run.sh").is_file():
        return package_root
    try:
        receipt = (prefix / "uv-receipt.toml").read_text(encoding="utf-8")
    except OSError:
        return package_root
    for source in re.findall(r'directory\s*=\s*"([^"]+)"', receipt):
        if (Path(source) / "run.sh").is_file():
            return Path(source)
    return package_root


PROJECT_ROOT = project_root(Path(__file__).resolve().parents[2], Path(sys.prefix))
RUNNER = PROJECT_ROOT / "run.sh"
DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d")
MAX_RANGE_DAYS = 366


def home_dir() -> Path:
    return Path(os.environ.get("MONITASK_CENTER_HOME") or Path.home() / ".monitask-center")


def config_path() -> Path:
    return home_dir() / "config.json"


def data_dir() -> Path:
    """Logs, lock and launchd output (unchanged location from earlier versions)."""
    return Path.home() / "Library/Application Support/MonitaskCenter"


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
    telegram: bool = True            # False = macOS notifications only
    update_check: bool = True        # background check for a newer release, offered after a command
    telegram_chat_id: str = ""       # an identifier, not a secret: stored here in plain JSON.
                                     # The bot token is NOT a field -- it lives in the OS
                                     # credential store, see monitask_center.telegram
    vacation: tuple[str, ...] = ()   # ISO dates with no checks and no reminders

    def check_hours(self) -> list[int]:
        return [*range(self.start_hour, self.end_hour, self.interval_hours), self.end_hour]

    def play_hours(self) -> set[int]:
        """Ticks that auto-press Play: the first check, and the one when lunch ends."""
        return {self.start_hour} | ({self.lunch_end_hour} if self.lunch_skip else set())

    def is_lunch(self, hour: int) -> bool:
        """Runtime decision only: lunch never changes the launchd schedule."""
        return self.lunch_skip and self.lunch_start_hour <= hour < self.lunch_end_hour


def max_interval(start: int, end: int) -> int:
    return max(1, end - start)


def validate(s: Settings) -> Settings:
    """Normalize language/vacation; raise ValueError for an impossible window."""
    if not 0 <= s.start_hour < s.end_hour <= 23:
        raise ValueError("hours")
    if not 1 <= s.interval_hours <= max_interval(s.start_hour, s.end_hour):
        raise ValueError("interval")
    if not 0 <= s.lunch_start_hour < s.lunch_end_hour <= 24:
        raise ValueError("lunch")
    language = s.language if s.language in i18n.available() else i18n.DEFAULT_LANGUAGE
    days = sorted({d.isoformat() for d in map(parse_date, s.vacation) if d})
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
