"""Logging and notifications (stdlib only): Telegram via tg-send.sh, macOS fallback."""

from __future__ import annotations

import functools
import os
import socket
import subprocess
from datetime import datetime
from pathlib import Path

from . import config
from .i18n import t

TG_SEND = Path.home() / ".claude/scripts/tg-send.sh"


def log(message: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}"
    try:
        config.data_dir().mkdir(parents=True, exist_ok=True)
        with (config.data_dir() / "monitask.log").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    if os.environ.get("MONITASK_CENTER_LOG_STDOUT") == "1":  # stdout would corrupt TUI redraws
        print(line)


def run(command: list[str], timeout: int = 30, cwd=None) -> subprocess.CompletedProcess:
    return subprocess.run(command, text=True, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, timeout=timeout, check=False, cwd=cwd)


@functools.cache
def host_name() -> str:
    try:
        r = subprocess.run(["/usr/sbin/scutil", "--get", "ComputerName"],
                           text=True, capture_output=True, timeout=5, check=False)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return socket.gethostname() or "unknown-host"


# Text travels as argv (item 1 = message, item 2 = title), so it is never parsed as AppleScript.
OSASCRIPT = "/usr/bin/osascript"
OSASCRIPT_LINES = (
    "on run argv",
    "display notification (item 1 of argv) with title (item 2 of argv)",
    "end run",
)


def osascript_command(message: str, title: str = "Monitask") -> list[str]:
    cmd = [OSASCRIPT]
    for line in OSASCRIPT_LINES:
        cmd += ["-e", line]
    return [*cmd, "--", message, title]


def macos_notification(message: str, title: str = "Monitask") -> None:
    """Best effort; a failure is logged (never raised) so it cannot hide silently."""
    try:
        r = subprocess.run(osascript_command(message, title), text=True, capture_output=True,
                           timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(t("notify.macos_error", error=exc))
        return
    if r.returncode != 0:
        log(t("notify.macos_failed", code=r.returncode, detail=(r.stderr or "").strip()))


def telegram(message: str) -> bool:
    if not TG_SEND.is_file():
        return False
    try:
        # tg-send.sh locates .env via git rev-parse, so cwd must be this checkout.
        # TG_BOT_TOKEN / TG_CHAT_ID come from .env loaded by run.sh.
        cwd = config.PROJECT_ROOT
        if run(["/bin/bash", str(TG_SEND), "preflight"], timeout=12, cwd=cwd).returncode != 0:
            return False
        return run(["/bin/bash", str(TG_SEND), "send", message], timeout=45, cwd=cwd).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def notify(message: str, use_telegram: bool = True) -> None:
    delivered = False
    if use_telegram:
        try:
            delivered = telegram(f"{message}\n🖥️ {host_name()}")
        except Exception as exc:  # noqa: BLE001 - a notification must never crash a job
            log(t("notify.telegram_error", error=exc))
    if not delivered:
        macos_notification(message)
