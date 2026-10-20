"""launchd LaunchAgents: render, detect drift, (re)install, remove.

Jobs `check` (StartCalendarInterval at each configured hour) and `overtime`, both running
config.RUNNER. Lunch is decided at runtime, so it never appears in the plist. Drift = the plist rendered from
current settings differs byte-for-byte from the installed file; only drifted jobs
are reloaded.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path

from . import config
from .i18n import t

PREFIX = "com.monitask-center"
JOBS = ("check", "overtime")
# Jobs from the shift-schedule era and older bundle ids; removed on install/uninstall.
LEGACY_JOBS = ("morning", "lunch", "afternoon", "evening", "night-start", "night-stop", "watch")
LEGACY_PREFIXES = (PREFIX, "com.dannylin.monitask-center", "com.dannylin.monitask")


def agents_dir() -> Path:
    return Path.home() / "Library/LaunchAgents"


def label(job: str) -> str:
    return f"{PREFIX}.{job}"


def plist_path(job: str, prefix: str = PREFIX) -> Path:
    return agents_dir() / f"{prefix}.{job}.plist"


def render(job: str, settings: config.Settings) -> bytes:
    out = config.data_dir()
    spec: dict = {
        "Label": label(job),
        "ProgramArguments": ["/bin/bash", str(config.RUNNER), job],
        "StandardOutPath": str(out / f"launchd.{job}.out"),
        "StandardErrorPath": str(out / f"launchd.{job}.err"),
        "ProcessType": "Interactive",
    }
    if job == "overtime":  # always scheduled; overtime_prompt is decided at run time
        minutes = settings.overtime_minutes
        spec["StartCalendarInterval"] = [{"Hour": minutes // 60, "Minute": minutes % 60}]
    else:
        spec["StartCalendarInterval"] = [{"Hour": h, "Minute": 0} for h in settings.check_hours()]
    return plistlib.dumps(spec)


def is_installed() -> bool:
    return any(plist_path(job).exists() for job in JOBS)


def stale_jobs(settings: config.Settings) -> list[str]:
    def current(job: str) -> bool:
        try:
            return plist_path(job).read_bytes() == render(job, settings)
        except OSError:
            return False

    return [job for job in JOBS if not current(job)]


def runner(job: str) -> str | None:
    """The script an installed job's plist runs (``ProgramArguments[1]``), or ``None``."""
    try:
        return plistlib.loads(plist_path(job).read_bytes())["ProgramArguments"][1]
    except (OSError, ValueError, KeyError, IndexError, TypeError, plistlib.InvalidFileException):
        return None


def foreign_jobs() -> list[str]:
    """Installed jobs that run something other than config.RUNNER (a 1.5.0-and-older checkout)."""
    return [job for job in JOBS if plist_path(job).exists() and runner(job) != str(config.RUNNER)]


DETAIL_MAX = 80


class LaunchdError(subprocess.CalledProcessError):
    """A checked launchctl call failed. Carries the exit code and the first stderr line
    (``detail``); str() is the localized one-liner, never the argv list."""

    def __init__(self, returncode: int, cmd: list[str], detail: str = "") -> None:
        super().__init__(returncode, cmd)
        self.detail = detail

    def __str__(self) -> str:
        return describe_error(self)


def describe_error(exc: BaseException) -> str:
    """Localized one-line reason for a launchd failure (exit code + short detail, no argv)."""
    if not isinstance(exc, subprocess.CalledProcessError):
        return str(exc)
    detail = getattr(exc, "detail", "")
    if detail:
        return t("launchd.err.detail", code=exc.returncode, detail=detail)
    return t("launchd.err.code", code=exc.returncode)


def _first_line(text: str | None) -> str:
    line = next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")
    return "".join(ch for ch in line if ch.isprintable())[:DETAIL_MAX]


def _launchctl(*args: str, check: bool = False) -> None:
    """Run launchctl. stderr is never inherited: a checked failure raises LaunchdError instead,
    so launchctl text can't land on a drawn TUI frame."""
    cmd = ["launchctl", *args]
    r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE if check else subprocess.DEVNULL,
                       text=True, errors="replace")
    if check and r.returncode:
        raise LaunchdError(r.returncode, cmd, _first_line(r.stderr))


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _cleanup_legacy() -> None:
    for prefix in LEGACY_PREFIXES:
        for job in (*LEGACY_JOBS, *JOBS):
            if prefix == PREFIX and job in JOBS:
                continue
            path = plist_path(job, prefix)
            if path.exists():
                _launchctl("bootout", _domain(), str(path))
                path.unlink(missing_ok=True)


def install(settings: config.Settings, jobs: list[str] | None = None) -> list[str]:
    """Write and (re)load *jobs* (default: all). On bootstrap failure the plist is rolled back and
    LaunchdError re-raised."""
    if not config.RUNNER.is_file():
        # "Update now" in 1.5.0 calls sync() from the new code before anything built the runtime.
        from .install import (  # lazy: install imports launchd
            InstallError,
            ensure_runtime,
        )

        try:
            ensure_runtime()
        except InstallError as exc:  # callers handle OSError; keep the reason as the cause
            raise FileNotFoundError(config.RUNNER) from exc
    jobs = list(JOBS) if jobs is None else jobs
    _cleanup_legacy()
    agents_dir().mkdir(parents=True, exist_ok=True)
    config.data_dir().mkdir(parents=True, exist_ok=True)
    for job in jobs:
        path = plist_path(job)
        try:
            previous = path.read_bytes()
        except OSError:
            previous = None
        path.write_bytes(render(job, settings))
        _launchctl("bootout", _domain(), str(path))
        try:
            _launchctl("bootstrap", _domain(), str(path), check=True)
        except Exception:
            # Never leave a plist that matches the new settings while nothing is loaded:
            # sync()/stale_jobs() must keep seeing this job as missing or stale.
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(previous)
                _launchctl("bootstrap", _domain(), str(path))  # best effort: bring the old job back
            raise
    return jobs


def uninstall() -> list[str]:
    removed = []
    for job in JOBS:
        path = plist_path(job)
        _launchctl("bootout", _domain(), str(path))
        if path.exists():
            path.unlink()
            removed.append(job)
    _cleanup_legacy()
    return removed


def sync(settings: config.Settings) -> tuple[str, list[str]]:
    """After a settings change: ("not_installed"|"unchanged"|"reinstalled", jobs)."""
    if not is_installed():
        return "not_installed", []
    stale = stale_jobs(settings)
    if not stale:
        return "unchanged", []
    return "reinstalled", install(settings, stale)


def install_missing(settings: config.Settings) -> str | None:
    """Upgrade migration, run by the new code itself: install jobs a release added that have no plist.

    "Update now" in 1.4.0 and older never synced launchd, so a job added since (overtime) stays
    uninstalled. A job that has a plist is never booted out, so the running check job is safe.
    -> a line to log/print when it did something, else None."""
    if not is_installed():
        return None
    missing = [job for job in JOBS if not plist_path(job).exists()]
    if not missing:
        return None
    try:
        return sync_message("reinstalled", install(settings, missing))
    except (subprocess.CalledProcessError, OSError) as exc:
        return t("launchd.err.migrate", error=describe_error(exc))


def sync_message(state: str, jobs: list[str]) -> str:
    """User-facing text for the result of sync()."""
    return t(f"launchd.sync.{state}", jobs=", ".join(jobs))


def loaded() -> list[str]:
    r = subprocess.run(["launchctl", "list"], text=True, capture_output=True, check=False)
    ours = {label(job) for job in JOBS}
    # `launchctl list` rows are "PID<tab>Status<tab>Label": match the label column exactly
    # (a prefix match would also count com.monitask-center.activity).
    return [line for line in r.stdout.splitlines()
            if len(cols := line.split(None, 2)) == 3 and cols[2].strip() in ours]
