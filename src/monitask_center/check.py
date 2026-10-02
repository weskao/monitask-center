"""Hourly check decision (pure) and the last-check record (stdlib only)."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from . import config

STATES = ("running", "timing", "paused", "hidden", "unknown", "not-running")
QUIET_STATES = ("running", "timing", "hidden")  # hidden = Monitask hides its window while timing


def decide(hour: int, settings: config.Settings, workday: tuple[bool, str], state: str) -> str:
    """-> outside | skip_vacation | skip_holiday | skip_lunch | ok | remind | warn_unknown."""
    if not settings.start_hour <= hour <= settings.end_hour:
        return "outside"
    is_workday, reason = workday
    if not is_workday:
        return "skip_vacation" if reason == "vacation" else "skip_holiday"
    if settings.is_lunch(hour):
        return "skip_lunch"
    if state in QUIET_STATES:
        return "ok"
    return "remind" if state in ("paused", "not-running") else "warn_unknown"


def last_check_path() -> Path:
    return config.home_dir() / "last-check.json"


def record_last_check(action: str, state: str, now: datetime | None = None) -> None:
    path = last_check_path()
    data = {"time": (now or datetime.now()).isoformat(timespec="seconds"), "action": action, "state": state}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(data, ensure_ascii=False) + "\n")
            os.chmod(tmp, 0o644)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    except OSError:
        pass  # bookkeeping must never fail a check


def last_check() -> dict | None:
    """{"time", "action", "state"} or None when missing/corrupt."""
    try:
        data = json.loads(last_check_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and all(isinstance(data.get(k), str) for k in ("time", "action", "state")):
        return {k: data[k] for k in ("time", "action", "state")}
    return None
