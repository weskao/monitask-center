"""Read-only status snapshot (stdlib only). Never starts, stops or clicks anything."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from . import check, config, launchd, workday
from .i18n import t

OK_STATES = ("running", "timing", "hidden")  # hidden = Monitask hides its window while timing
MAX_VACATION_SHOWN = 5
NEXT_LOOKAHEAD_DAYS = 14
ACTIVITY_FILE = "activity-today.json"  # under config.home_dir(); activity/fetch.js writes it on every fetch


@dataclass(frozen=True)
class Row:
    key: str     # i18n key suffix of the label, e.g. "workday"
    label: str
    value: str
    level: str   # ok | warn | info | error
    mark: str = ""  # the part of value the TUI paints in the level's colour


def parse_kv(text: str) -> dict[str, str]:
    pairs = (line.split("=", 1) for line in text.splitlines() if "=" in line)
    return {k.strip(): v.strip() for k, v in pairs}


def run_status(timeout: int = 20) -> tuple[int, str]:
    """`bash run.sh status` -> (returncode, combined output); (1, "") when it cannot run at all."""
    try:
        r = subprocess.run(["/bin/bash", str(config.RUNNER), "status"], text=True,
                           capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def runner_status() -> dict[str, str] | None:
    """Parsed key=value status, or None when the runner is missing/not working."""
    code, out = run_status()
    data = parse_kv(out)
    return data if code == 0 and "state" in data else None


# Internal code -> catalog key (literal keys, so the i18n guard tests see them as used).
REASON_KEYS = {
    "workday": "status.reason.workday",
    "vacation": "status.reason.vacation",
    "calendar-unavailable(fail-open)": "status.reason.calendar-unavailable(fail-open)",
    "date-not-in-calendar(fail-open)": "status.reason.date-not-in-calendar(fail-open)",
    "error(fail-open)": "status.reason.error(fail-open)",
}
STATE_KEYS = {
    "running": "status.state.running",
    "timing": "status.state.timing",
    "paused": "status.state.paused",
    "hidden": "status.state.hidden",
    "not-running": "status.state.not-running",
    "unknown": "status.state.unknown",
    "n/a": "status.state.n/a",
}
ACTION_KEYS = {
    "ok": "status.action.ok",
    "remind": "status.action.remind",
    "warn_unknown": "status.action.warn_unknown",
    "play": "status.action.play",
    "pause": "status.action.pause",
    "play_failed": "status.action.play_failed",
    "pause_failed": "status.action.pause_failed",
    "skip_vacation": "status.action.skip_vacation",
    "skip_holiday": "status.action.skip_holiday",
    "skip_lunch": "status.action.skip_lunch",
    "outside": "status.action.outside",
    "locked": "status.action.locked",
}


def reason_text(reason: str) -> str:
    """workday.decide() reason -> user text. Calendar descriptions are data and stay as is."""
    head, sep, desc = reason.partition(":")
    if head == "holiday":
        return t("status.reason.weekend") if desc == "weekend" else t("status.reason.holiday", desc=desc)
    if head == "workday" and sep:
        return t("status.reason.workday_desc", desc=desc)
    return t(REASON_KEYS[reason]) if reason in REASON_KEYS else reason


def state_text(state: str) -> str:
    return t(STATE_KEYS.get(state, "status.state.unknown"))


def action_text(action: str) -> str:
    return t(ACTION_KEYS[action]) if action in ACTION_KEYS else action


def workday_line(is_workday: bool, reason: str) -> str:
    verdict = t("status.workday.yes" if is_workday else "status.workday.no")
    return t("status.workday.line", verdict=verdict, reason=reason_text(reason))


def _next_check(now: datetime, settings: config.Settings) -> datetime | None:
    """Next tick that will really act: a workday hour outside the lunch window."""
    hours = [h for h in settings.check_hours() if not settings.is_lunch(h)]
    for offset in range(NEXT_LOOKAHEAD_DAYS):
        day = (now + timedelta(days=offset)).date()
        ticks = [datetime(day.year, day.month, day.day, h) for h in hours]
        ticks = [tick for tick in ticks if tick > now]
        if ticks and workday.peek_status(day, settings)[0]:
            return ticks[0]
    return None


def _launchd_row(settings: config.Settings) -> Row:
    label = t("status.label.launchd")
    if not launchd.is_installed():
        return Row("launchd", label, t("status.launchd.not_installed"), "warn")
    try:
        loaded = bool(launchd.loaded())
    except OSError:
        loaded = False
    if not loaded:
        return Row("launchd", label, t("status.launchd.not_loaded"), "warn")
    if launchd.stale_jobs(settings):
        return Row("launchd", label, t("status.launchd.stale"), "warn")
    return Row("launchd", label, t("status.launchd.loaded"), "ok")


def _activity_row(today: date) -> Row | None:
    """Today's activity rate as last fetched by the optional activity job; None when there is none for today."""
    try:
        data = json.loads((config.home_dir() / ACTIVITY_FILE).read_text(encoding="utf-8"))
        rate = float(data["daily"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if data.get("date") != today.isoformat():
        return None
    mark = f"{data['daily']}%"
    level = "error" if rate < 60 else "warn" if rate < 80 else "ok"  # same tones as activity/fetch.js
    return Row("activity", t("status.label.activity"),
               t("status.activity.value", rate=mark, time=data.get("time", "")), level, mark)


def snapshot(settings: config.Settings, now: datetime | None = None) -> list[Row]:
    """Status rows, most important first (a small terminal truncates the tail).

    Spawns `run.sh status` (~1 python start): cache the result in a TUI. Read-only: the
    calendar is peeked, so opening a status view never sends a notification."""
    now = now or datetime.now()
    today = now.date()
    is_workday, reason = workday.peek_status(today, settings)
    rows: list[Row] = []

    data = runner_status()
    unknown = t("status.unknown")
    if data is None:
        rows.append(Row("monitask", t("status.label.monitask"), unknown, "warn"))
    else:
        state = data.get("state", "unknown")
        rows.append(Row("monitask", t("status.label.monitask"), state_text(state),
                        "ok" if state in OK_STATES else "warn"))
    if activity := _activity_row(today):
        rows.append(activity)

    last = check.last_check()
    rows.append(Row("last_check", t("status.label.last_check"),
                    t("status.last_check.value", time=last["time"], action=action_text(last["action"]),
                      state=state_text(last["state"])) if last else t("status.last_check.none"),
                    "info"))

    nxt = _next_check(now, settings)
    rows.append(Row("next", t("status.label.next"),
                    t("status.next.value", when=f"{nxt:%Y-%m-%d %H:%M}") if nxt else t("status.next.none"),
                    "info" if nxt else "warn"))

    rows.append(Row("workday", t("status.label.workday"), workday_line(is_workday, reason),
                    "ok" if is_workday else "info"))
    rows.append(_launchd_row(settings))

    if data is None:
        rows.append(Row("accessibility", t("status.label.accessibility"), unknown, "warn"))
    else:
        trusted = data.get("accessibility_trusted") == "True"
        rows.append(Row("accessibility", t("status.label.accessibility"),
                        t("status.ax.yes" if trusted else "status.ax.no"), "ok" if trusted else "warn"))

    # Settings echo (already visible as TUI rows): last, so they are what truncation drops.
    marks = [t("status.hours.lunch_mark" if settings.is_lunch(h) else "status.hours.plain", hour=h)
             for h in settings.check_hours()]
    rows.append(Row("hours", t("status.label.hours"), " ".join(marks), "info"))

    lunch = (t("status.lunch.window", start=settings.lunch_start_hour, end=settings.lunch_end_hour)
             if settings.lunch_skip else t("status.lunch.off"))
    rows.append(Row("lunch", t("status.label.lunch"), lunch, "info"))

    upcoming = [v for v in settings.vacation if v >= today.isoformat()]
    shown = ", ".join(upcoming[:MAX_VACATION_SHOWN]) or t("status.vacation.none")
    if len(upcoming) > MAX_VACATION_SHOWN:
        shown += " " + t("status.vacation.more", count=len(upcoming) - MAX_VACATION_SHOWN)
    rows.append(Row("vacation", t("status.label.vacation"), shown, "info"))
    return rows


def status_lines(settings: config.Settings, now: datetime | None = None) -> list[str | tuple[str, str, str]]:
    """Plain lines; a row with a mark is (line, mark, level) so the TUI can colour the mark."""
    lines: list[str | tuple[str, str, str]] = []
    for r in snapshot(settings, now):
        line = f"{r.label}: {r.value}"
        lines.append((line, r.mark, r.level) if r.mark else line)
    return lines
