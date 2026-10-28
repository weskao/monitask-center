"""monitask-center command line: `--config` TUI plus thin read-only subcommands (stdlib only)."""

from __future__ import annotations

import argparse
import functools
import json
import os
import subprocess
import sys
from datetime import datetime
from importlib import metadata
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from . import __version__, config, i18n, install, launchd, notify, status, update_check
from .i18n import t

COMMANDS = ("install", "uninstall", "status", "plan", "check", "dump-windows", "logs", "update", "activity")
EXEC_COMMANDS = ("check", "dump-windows", "logs", "activity")  # replace this process (or run under launchd): no update check
REPO = "weskao/monitask-center"  # public: releases come from the unauthenticated GitHub API


def _normalize_argv(argv: list[str]) -> list[str]:
    """`config` == `--config`, and every command also answers to `--<command>` (`--update` == `update`)."""
    if argv and argv[0] == "config":
        return ["--config", *argv[1:]]
    if argv and argv[0].removeprefix("--") in COMMANDS:
        return [argv[0].removeprefix("--"), *argv[1:]]
    return argv


class _Formatter(argparse.HelpFormatter):
    """argparse's own English labels ("usage:", "options:") come from the catalog instead."""

    SECTIONS = {"options": "cli.section.options", "optional arguments": "cli.section.options",
                "positional arguments": "cli.section.positional"}

    def add_usage(self, usage, actions, groups, prefix=None):
        super().add_usage(usage, actions, groups, t("cli.usage_prefix") + " " if prefix is None else prefix)

    def start_section(self, heading):
        super().start_section(t(self.SECTIONS[heading]) if heading in self.SECTIONS else heading)


class _Parser(argparse.ArgumentParser):
    # ponytail: only the "error:" prefix is localized; argparse's own messages
    # ("invalid choice", "unrecognized arguments") stay English.
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, t("cli.error", prog=self.prog, message=message) + "\n")


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="monitask-center", description=t("cli.description"), epilog=t("cli.epilog"),
                allow_abbrev=False, add_help=False, formatter_class=_Formatter)
    p.add_argument("-h", "--help", action="help", help=t("cli.help.help"))
    p.add_argument("--config", action="store_true", help=t("cli.help.config"))
    sub = p.add_subparsers(dest="command", metavar="{" + ",".join(COMMANDS) + "}")
    for name in COMMANDS:
        sp = sub.add_parser(name, help=t(f"cli.help.{name}"), description=t(f"cli.help.{name}"),
                            allow_abbrev=False, add_help=False, formatter_class=_Formatter)
        if name != "update":  # `update` takes no arguments at all: `update --help` must not install
            sp.add_argument("-h", "--help", action="help", help=t("cli.help.help"))
        if name == "logs":
            sp.add_argument("--path", action="store_true", help=t("cli.help.logs_path"))
    return p


def log_path() -> Path:
    return config.data_dir() / "monitask.log"


def _exec(path: str, args: list[str]) -> int:
    """Replace this process; only returns (1) when exec itself fails."""
    try:
        os.execv(path, args)
    except OSError as exc:
        print(t("cli.err.exec", error=exc), file=sys.stderr)
    return 1


def cmd_status() -> int:
    for row in status.snapshot(config.load()):
        print(f"{row.label}: {row.value}")
    return 0


def cmd_plan(now: datetime | None = None) -> int:
    """Today's verdict, configured hours (lunch marked), what would run, next check. No Monitask access."""
    from . import workday

    now = now or datetime.now()
    s = config.load()
    ok, reason = workday.peek_status(now.date(), s)  # read-only: never notifies
    print(f"{t('status.label.workday')}: {status.workday_line(ok, reason)}")
    hours = s.check_hours()
    marks = [t("status.hours.lunch_mark" if s.is_lunch(h) else "status.hours.plain", hour=h) for h in hours]
    print(f"{t('status.label.hours')}: {' '.join(marks)}")
    runs = [h for h in hours if not s.is_lunch(h)] if ok else []
    runs_text = " ".join(t("status.hours.plain", hour=h) for h in runs) or t("cli.plan.none")
    print(f"{t('cli.plan.runs')}: {runs_text}")
    nxt = status._next_check(now, s)
    nxt_text = t("status.next.value", when=f"{nxt:%Y-%m-%d %H:%M}") if nxt else t("status.next.none")
    print(f"{t('status.label.next')}: {nxt_text}")
    return 0


def cmd_logs(path_only: bool) -> int:
    path = log_path()
    if path_only:
        print(path)
        return 0
    if not path.is_file():
        print(t("cli.logs.missing", path=path), file=sys.stderr)
        return 1
    return _exec("/usr/bin/tail", ["tail", "-n", "50", "-f", str(path)])


def cmd_activity() -> int:
    """Send the activity rate now, via the checkout the optional activity job was installed from."""
    script = launchd.runner("activity")
    if not script or not Path(script).is_file():
        print(t("cli.activity.missing"), file=sys.stderr)
        return 1
    return _exec("/bin/bash", ["/bin/bash", script, "now"])


def cmd_config() -> int:
    from .tui import menu  # lazy: the TUI is only needed here

    # `run.sh status` costs a python start: cache it for the session (settings are re-read each call).
    status.runner_status = functools.cache(status.runner_status)
    fetch = status.start_activity_fetch()

    def fetching() -> bool:
        return fetch is not None and fetch.poll() is None

    return menu.config_menu(status_lines=lambda: status.status_lines(config.load(), fetching=fetching()),
                            busy=fetching)


def _update_cache_path() -> Path:
    return config.home_dir() / "update-check.json"


def _start_update_check(argv: list[str]) -> update_check.Started | None:
    """Kick off the release check in the background so it overlaps the command."""
    try:
        if (argv[:1] and argv[0] in (*EXEC_COMMANDS, "update")) or not config.load().update_check:
            return None
        return update_check.start(REPO, __version__, cache_path=_update_cache_path(),
                                  fetch=update_check.fetch_github)
    except Exception:  # noqa: BLE001 - a hint must never fail the command
        return None


#: Runs in the NEW code after the reinstall: move/refresh the launchd runtime (run.sh embeds the
#: package path, which a new tool venv can change), then install jobs a release added or changed.
_MIGRATE = ("from monitask_center import config, install, launchd; s = config.load(); "
            "m = install.migrate(s); m and print(m); launchd.sync(s)")


#: What "Update now" runs: reinstall the CLI from the release tag (no clone needed, never main's
#: unreleased commits), then migrate with the new code. sys.executable is the tool venv's python,
#: whose path survives the reinstall.
def _upgrade(found: update_check.UpdateAvailable) -> list[list[str]]:
    return [["uv", "tool", "install", "--force", "--from", _install_source(found.latest), "monitask-center"],
            [sys.executable, "-c", _MIGRATE]]


def _install_source(tag: str) -> str:
    return f"git+https://github.com/{REPO}.git@{tag}"


_RELEASE_NOTES = f"https://github.com/{REPO}/releases/tag/%s"


def _hint_update(found: update_check.UpdateAvailable) -> str:
    """The ``ask`` off a terminal (pipes): a one-shot hint on stderr, never a block."""
    print(t("update.available", latest=found.latest, current=found.current), file=sys.stderr)
    print(t("update.command", source=_install_source(found.latest)), file=sys.stderr)
    return update_check.SKIP


def _ask_update(found: update_check.UpdateAvailable) -> str:
    from .tui import menu  # lazy: the TUI is only needed here

    return menu.update_prompt(found, _RELEASE_NOTES % found.latest, stdin=sys.stdin, out=sys.stderr)


def _offer_update(started: update_check.Started | None) -> None:
    """After the command: ask (or hint) about a newer release. Never changes the exit code."""
    if started is None:
        return
    from .tui.keyreader import is_interactive

    ask = _ask_update if is_interactive(sys.stdin, sys.stderr) else _hint_update
    answer = update_check.offer(started, ask, cache_path=_update_cache_path(), upgrade=_upgrade)
    if answer == update_check.UPGRADE_FAILED:
        found = update_check.collect(started)  # already finished: the offer just used it
        print(t("update.failed", source=_install_source(found.latest if found else "vX.Y.Z")), file=sys.stderr)


def _direct_url() -> dict | None:
    """PEP 610 record of how this install was made (VCS ref, local dir), or None (index/unknown)."""
    try:
        data = json.loads(metadata.distribution("monitask-center").read_text("direct_url.json") or "null")
    except (metadata.PackageNotFoundError, OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def cmd_update() -> int:
    """Always end on the newest release tag: what "Update now" runs, unless already installed from it."""
    try:
        latest = update_check._latest_from_body(update_check.fetch_github(REPO, 10))
    except (OSError, ValueError):  # URLError and timeouts are OSErrors
        latest = None
    if not latest:
        print(t("update.cmd.offline"), file=sys.stderr)
        return 1
    direct = _direct_url()
    # No record = an index install: trust the version. A VCS install counts only when pinned to this tag.
    pinned = direct is None or (direct.get("vcs_info") or {}).get("requested_revision") == latest
    if pinned and __version__ == latest.removeprefix("v"):
        print(t("update.cmd.latest", current=__version__, latest=latest))
        return 0
    steps = _upgrade(update_check.UpdateAvailable(current=__version__, latest=latest))
    command = " ".join(steps[0])
    print(t("update.cmd.updating", current=__version__, latest=latest, command=command), flush=True)
    for step in steps:  # same as update_check.offer's UPDATE_NOW: stop at the first failure
        try:
            ok = subprocess.run(step, check=False).returncode == 0
        except OSError:
            ok = False
        if not ok:
            print(t("update.cmd.failed", command=command), file=sys.stderr)
            return 1
    print(t("update.cmd.done", latest=latest))
    if direct and "dir_info" in direct:
        print(t("update.cmd.local", path=url2pathname(urlparse(direct.get("url", "")).path)))
    return 0


def main(argv: list[str] | None = None) -> int:
    """The update check starts first and is offered last, whichever way the command exits."""
    raw = _normalize_argv(list(sys.argv[1:] if argv is None else argv))
    started = _start_update_check(raw)
    try:
        return _main(raw)
    finally:
        _offer_update(started)


def _main(raw: list[str]) -> int:
    i18n.set_language(config.load().language)  # before the parser so --help is localized
    parser = build_parser()
    args = parser.parse_args(raw)
    if args.command not in ("install", "uninstall", *EXEC_COMMANDS):  # check migrates under launchd
        # install.migrate first: it builds the runtime sync_drifted needs (and moves the activity job).
        # Notes also go to monitask.log, like the hourly check's, so an upgrade stays traceable later.
        for migrate in (lambda: install.migrate(config.load()), lambda: launchd.sync_drifted(config.load())):
            if note := migrate():
                print(note, file=sys.stderr)
                notify.log(note)
    if args.config:
        return cmd_config()
    match args.command:
        case None:
            parser.print_help()
            return 0
        case "install":
            return install.run_install(config.load())
        case "uninstall":
            return install.uninstall()
        case "status":
            return cmd_status()
        case "plan":
            return cmd_plan()
        case "logs":
            return cmd_logs(args.path)
        case "update":
            return cmd_update()
        case "activity":
            return cmd_activity()
        case cmd:  # check, dump-windows: the read-only AX engine runs under run.sh
            return _exec("/bin/bash", ["/bin/bash", str(config.RUNNER), cmd])
