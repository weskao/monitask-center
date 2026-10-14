"""Logging and notifications: Telegram via telegram_kit, macOS fallback.

The token is read from the OS credential store (falling back to ``TG_BOT_TOKEN`` in the
environment, which run.sh populates from ``.env``), so no notification path needs a
helper script or a checkout-relative ``.env``.
"""

from __future__ import annotations

import functools
import os
import socket
import subprocess
from datetime import datetime

from . import config, telegram
from .i18n import t


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


def telegram_message(message: str, chat_id: str = "") -> bool:
    """Send one Telegram message. ``False`` on missing credentials or any failure.

    Credentials come from :func:`monitask_center.telegram.send`, so a token saved through
    ``--config`` and one left in ``.env`` are both honoured, in that order.
    """
    return telegram.send(message, chat_id or config.load().telegram_chat_id)


def notify(message: str, use_telegram: bool = True, chat_id: str = "") -> None:
    delivered = False
    if use_telegram:
        try:
            delivered = telegram_message(f"{message}\n🖥️ {host_name()}", chat_id)
        except Exception as exc:  # noqa: BLE001 - a notification must never crash a job
            log(t("notify.telegram_error", error=exc))
    if not delivered:
        macos_notification(message)
