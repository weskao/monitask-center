"""monitask-center command line: `--config` TUI plus thin read-only subcommands (stdlib only)."""

from __future__ import annotations

import argparse
import functools
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import __version__, config, i18n, install, status, update_check
from .i18n import t

COMMANDS = ("install", "uninstall", "status", "plan", "check", "dump-windows", "logs")
EXEC_COMMANDS = ("check", "dump-windows", "logs")  # replace this process (or run under launchd): no update check


def _normalize_argv(argv: list[str]) -> list[str]:
    """`monitask-center config` == `monitask-center --config`."""
    if argv and argv[0] == "config":
        return ["--config", *argv[1:]]
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
    p = _Parser(prog="monitask-center", description=t("cli.description"), allow_abbrev=False,
                add_help=False, formatter_class=_Formatter)
    p.add_argument("-h", "--help", action="help", help=t("cli.help.help"))
    p.add_argument("--config", action="store_true", help=t("cli.help.config"))
    sub = p.add_subparsers(dest="command", metavar="{" + ",".join(COMMANDS) + "}")
    for name in COMMANDS:
        sp = sub.add_parser(name, help=t(f"cli.help.{name}"), description=t(f"cli.help.{name}"),
                            allow_abbrev=False, add_help=False, formatter_class=_Formatter)
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


def cmd_config() -> int:
    from .tui import menu  # lazy: the TUI is only needed here

    # `run.sh status` costs a python start: cache it for the session (settings are re-read each call).
    status.runner_status = functools.cache(status.runner_status)
    return menu.config_menu(status_lines=lambda: status.status_lines(config.load()))


def _update_cache_path() -> Path:
    return config.home_dir() / "update-check.json"


def _fetch_git_tags(_dist: str, timeout: float) -> str:
    """The newest `vX.Y.Z` tag on origin as a release body for update_check. Uses git (and the
    user's own credentials) because the repo is private, so the unauthenticated GitHub API 404s."""
    cmd = ["git", "-C", str(config.PROJECT_ROOT), "ls-remote", "--tags", "--refs", "origin"]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=True,
                              env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    except subprocess.SubprocessError as exc:
        raise OSError(str(exc)) from exc  # the one error family update_check treats as "offline"
    tags = re.findall(r"refs/tags/(v?\d+(?:\.\d+)+)$", done.stdout, re.M)
    if not tags:
        raise ValueError("no release tags")
    newest = max(tags, key=lambda tag: tuple(int(n) for n in tag.lstrip("v").split(".")))
    return json.dumps({"tag_name": newest})


def _start_update_check(argv: list[str]) -> update_check.Started | None:
    """Kick off the release check in the background so it overlaps the command."""
    try:
        if (argv[:1] and argv[0] in EXEC_COMMANDS) or not config.load().update_check:
            return None
        return update_check.start("monitask-center", __version__, cache_path=_update_cache_path(),
                                  timeout=3.0, fetch=_fetch_git_tags)  # ls-remote ~1s: above the 0.8s default
    except Exception:  # noqa: BLE001 - a hint must never fail the command
        return None


_SYNC_LAUNCHD = "from monitask_center import config, launchd; launchd.sync(config.load())"


#: What "Update now" runs: pull the checkout, reinstall it editable (never a copy in the tool venv, so the
#: CLI and the launchd runner both run the checkout), then sync launchd in the NEW code so a job added by
#: the release gets installed. sys.executable is the tool venv's python, which the reinstall just rebuilt.
def _upgrade(_found: update_check.UpdateAvailable) -> list[list[str]]:
    root = str(config.PROJECT_ROOT)
    return [["git", "-C", root, "pull", "--ff-only"], ["uv", "tool", "install", "--editable", root, "--force"],
            [sys.executable, "-c", _SYNC_LAUNCHD]]


_RELEASE_NOTES = "https://github.com/weskao/monitask-center/releases/tag/%s"


def _hint_update(found: update_check.UpdateAvailable) -> str:
    """The ``ask`` off a terminal (pipes): a one-shot hint on stderr, never a block."""
    print(t("update.available", latest=found.latest, current=found.current), file=sys.stderr)
    print(t("update.command"), file=sys.stderr)
    return update_check.SKIP


def _ask_update(found: update_check.UpdateAvailable) -> str:
    from .tui import menu  # lazy: the TUI is only needed here

    return menu.update_prompt(found, _RELEASE_NOTES % found.latest, stdin=sys.stdin, out=sys.stderr)


def _offer_update(started: update_check.Started | None) -> None:
    """After the command: ask (or hint) about a newer release. Never changes the exit code."""
    if started is None:
        return
    from .tui.keyreader import is_interactive

    started.thread.join(3.0)  # offer() itself would stop waiting after 0.8s, before ls-remote returns
    ask = _ask_update if is_interactive(sys.stdin, sys.stderr) else _hint_update
    answer = update_check.offer(started, ask, cache_path=_update_cache_path(), upgrade=_upgrade)
    if answer == update_check.UPGRADE_FAILED:
        print(t("update.failed"), file=sys.stderr)


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
        case cmd:  # check, dump-windows: the read-only AX engine runs under run.sh
            return _exec("/bin/bash", ["/bin/bash", str(config.PROJECT_ROOT / "run.sh"), cmd])
