"""Telegram credentials: the bot token lives in the OS credential store, never in config.json.

One module owns every read and write of the token, so ``--config`` and
``monitask-center install`` cannot drift into two different storage mechanisms.

===========  ===========================================================
macOS        Keychain, via the ``security`` CLI
Linux        Secret Service (GNOME Keyring / KWallet), via ``secret-tool``
Windows      Credential Manager (DPAPI), via ``powershell.exe``
none         **nothing is stored** -- the environment is used instead
===========  ===========================================================

With no credential store this module refuses to write rather than falling back to a
plaintext file: an encoding anyone can reverse is not storage security, it is a
comforting lie. :func:`set_token` returns ``False`` and the caller points the user at
``TG_BOT_TOKEN`` instead.

The bot token is the only secret here. The chat id is an identifier rather than a
credential, so it lives in ``config.json`` as an ordinary :class:`~monitask_center.config.Settings`
field -- the same split ``ai-accounts`` makes, where only ``telegram_bot_token`` is
``masked=True``.

Delegated to :mod:`telegram_kit` (zero-dependency, stdlib-only): the store itself, the
per-value environment fallback in :func:`telegram_kit.resolve_credentials`, the Bot API
call, and the masking used for display. This module only decides *which* service name
and key the credentials are filed under, and keeps the token out of the config file.

Resolution order is **credential store > environment**. A token saved through
``--config`` therefore keeps working even when a stale ``TG_BOT_TOKEN`` lingers in a
shell profile or in ``.env`` -- the configured value wins deliberately.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import telegram_kit

#: Keychain service / Secret Service attribute identifying this program's items.
SERVICE = "monitask-center"
#: The account name the token is filed under, shared with ai-accounts and aicp.
TOKEN_KEY = telegram_kit.TOKEN_KEY
#: Longest a typed token / chat id may grow while being pasted.
MAX_TOKEN_LEN = telegram_kit.MAX_TOKEN_LEN
MAX_CHAT_ID_LEN = telegram_kit.MAX_CHAT_ID_LEN
#: The environment variables telegram_kit falls back to; the same keys .env uses.
ENV_KEYS = (telegram_kit.TOKEN_ENV, telegram_kit.CHAT_ID_ENV)

# A bot token is "<numeric id>:<35+ chars>"; a chat id is numeric, optionally negative
# for a supergroup. Same rules install.py already enforced on the .env path.
TOKEN_RE = re.compile(r"^[0-9]+:[A-Za-z0-9_-]+$")
CHAT_RE = re.compile(r"^-?[0-9]+$")


def _store() -> telegram_kit.CredentialStore:
    return telegram_kit.CredentialStore(SERVICE)


def available() -> bool:
    """Whether this machine has a credential store we can write to."""
    return telegram_kit.available()


def backend_label() -> str:
    """A human name for the active store ("macOS Keychain", ...), for --config and install."""
    return telegram_kit.backend_label()


def no_store_hint(key: str = TOKEN_KEY) -> str:
    """Why a token could not be saved, and the one thing the user can do instead."""
    env = telegram_kit.TOKEN_ENV if key == TOKEN_KEY else telegram_kit.CHAT_ID_ENV
    return (f"no credential store on this machine ({backend_label()}); "
            f"a secret is never written to the config file -- export {env} instead")


def token() -> str:
    """The stored bot token, or ``""``. Never raises: a locked keyring means "no token"."""
    try:
        return _store().get(TOKEN_KEY)
    except Exception:  # noqa: BLE001 - a locked store must not break the menu
        return ""


def set_token(value: str) -> bool:
    """Store *value* as the bot token; an empty value removes it.

    ``False`` when there is no credential store to hold it. Refuses rather than
    writing plaintext -- see the module docstring.
    """
    if not available():
        return False
    value = (value or "").strip()[:MAX_TOKEN_LEN]
    try:
        return _store().set(TOKEN_KEY, value)
    except Exception:  # noqa: BLE001 - a denied prompt is "not stored", not a crash
        return False


def mask(value: str) -> str:
    """*value* for display: stars plus at most the last 4 characters."""
    return telegram_kit.mask_secret(value)


def resolve(chat_id: str = "") -> tuple[str, str]:
    """``(token, chat_id)`` to send with: what is stored/configured, else the environment.

    Each value falls back on its own and blank counts as unset, so a store holding only
    a token still pairs with ``TG_CHAT_ID`` from ``.env``.
    """
    try:
        return telegram_kit.resolve_credentials(token(), chat_id)
    except Exception:  # noqa: BLE001 - resolution must not raise into the menu
        return "", ""


def send(text: str, chat_id: str = "") -> bool:
    """Send *text*. ``False`` on missing credentials or any failure; never raises."""
    tok, chat = resolve(chat_id)
    try:
        return telegram_kit.send_message(tok, chat, text)
    except Exception:  # noqa: BLE001 - a notification must never crash a job
        return False


def read_token(prompt: str) -> str | None:
    """Read a token without echo. ``None`` when echo cannot be disabled or input ends."""
    return telegram_kit.read_hidden(prompt)


# ── .env fallback ────────────────────────────────────────────────────────────
# run.sh sources .env into the environment, so a token left there keeps working with no
# code change. These helpers let install move one into the credential store and take it
# back out of the plaintext file, which is what --config and install share.


def env_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError:
        return []


def env_value(lines: list[str], key: str) -> str:
    for line in lines:
        if line.startswith(f"{key}="):
            return line[len(key) + 1:].rstrip("\r\n")
    return ""


def strip_env_keys(lines: list[str]) -> list[str]:
    """*lines* without the Telegram entries, with the final line newline-terminated."""
    kept = [ln for ln in lines if not ln.startswith(tuple(f"{k}=" for k in ENV_KEYS))]
    if kept and not kept[-1].endswith("\n"):
        kept[-1] += "\n"
    return kept


def write_env(path: Path, lines: list[str], body: str = "") -> None:
    """Rewrite *.env* owner-only and atomically, preserving every unrelated entry."""
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    old_mask = os.umask(0o077)
    try:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("".join(lines) + body)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    finally:
        os.umask(old_mask)


def migrate_env_token(path: Path) -> bool:
    """Move a valid ``TG_BOT_TOKEN`` from *.env* into the credential store and drop it.

    ``True`` only when a token was actually migrated, so callers can tell "nothing to do"
    from "moved". A store that already holds a token wins: the file is left alone rather
    than being able to overwrite the configured value.
    """
    if not available() or token():
        return False
    lines = env_lines(path)
    value = env_value(lines, telegram_kit.TOKEN_ENV).strip()
    if not TOKEN_RE.match(value) or not set_token(value):
        return False
    write_env(path, strip_env_keys(lines))
    return True