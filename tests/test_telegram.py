"""The credential-store layer: precedence, refusal without a store, and the .env fallback.

Nothing here touches a real Keychain: ``conftest.no_real_keychain`` stubs the public
helpers, and the tests that exercise them install a fake ``CredentialStore``.
"""

import os
import stat

import pytest

from monitask_center import telegram


class FakeStore:
    """The three methods telegram.py calls, mirroring CredentialStore's empty-value contract."""

    def __init__(self, items=None):
        self.items = dict(items or {})

    def get(self, key):
        return self.items.get(key, "")

    def set(self, key, value):
        if not value:  # the real store removes the item instead of storing a blank
            self.items.pop(key, None)
            return True
        self.items[key] = value
        return True

    def delete(self, key):
        return self.items.pop(key, None) is not None


@pytest.fixture
def store(monkeypatch):
    """Un-stub the public helpers so the real telegram.py logic runs against FakeStore."""
    for name in ("available", "backend_label", "token", "set_token", "send"):
        monkeypatch.setattr(telegram, name, getattr(telegram, f"_real_{name}"))
    fake = FakeStore()
    monkeypatch.setattr(telegram, "_store", lambda: fake)
    monkeypatch.setattr(telegram, "available", lambda: True)
    return fake


def test_service_and_key_are_the_shared_convention():
    # ai-accounts and aicp file the same key name under their own service, so the token
    # is readable by every tool that speaks the convention.
    assert telegram.TOKEN_KEY == "telegram_bot_token"
    assert telegram.SERVICE == "monitask-center"
    assert telegram.ENV_KEYS == ("TG_BOT_TOKEN", "TG_CHAT_ID")


def test_a_saved_token_round_trips_and_an_empty_one_deletes_it(store):
    assert telegram.set_token("123:abc") is True
    assert telegram.token() == "123:abc"
    assert store.items == {telegram.TOKEN_KEY: "123:abc"}
    telegram.set_token("")  # an empty value removes the item rather than storing a blank
    assert telegram.token() == "" and store.items == {}


def test_set_token_refuses_rather_than_writing_plaintext(store, monkeypatch):
    monkeypatch.setattr(telegram, "available", lambda: False)
    called = []
    monkeypatch.setattr(telegram, "_store", lambda: called.append(1))
    assert telegram.set_token("123:abc") is False
    assert called == []  # the store was never even opened


def test_a_locked_store_reads_as_no_token_rather_than_raising(store, monkeypatch):
    class Locked:
        def get(self, key):
            raise RuntimeError("interaction not allowed")

    monkeypatch.setattr(telegram, "_store", lambda: Locked())
    assert telegram.token() == ""


def test_resolve_prefers_the_store_over_a_stale_environment_variable(store, monkeypatch):
    telegram.set_token("999:from_store")
    monkeypatch.setenv(telegram.ENV_KEYS[0], "111:from_env")
    monkeypatch.setenv(telegram.ENV_KEYS[1], "-100")
    assert telegram.resolve("-42") == ("999:from_store", "-42")


def test_each_value_falls_back_on_its_own(store, monkeypatch):
    """A store holding only a token still pairs with TG_CHAT_ID from .env."""
    telegram.set_token("999:only_token")
    monkeypatch.setenv(telegram.ENV_KEYS[1], "-1001234")
    assert telegram.resolve("") == ("999:only_token", "-1001234")


def test_blank_counts_as_unset_so_a_stale_value_cannot_pin_the_bot(store, monkeypatch):
    telegram.set_token("999:real")
    monkeypatch.setenv(telegram.ENV_KEYS[0], "111:stale")
    assert telegram.resolve("") == ("999:real", "")


def test_mask_never_reveals_more_than_the_last_four_characters():
    assert telegram.mask("") == ""
    assert telegram.mask("short") == "*" * 8
    masked = telegram.mask("1234567890:AAAABBBBCCCC")
    assert masked.startswith("*") and masked.endswith("CCCC")
    assert "1234567890" not in masked


def test_format_rules_match_what_install_accepted_before():
    assert telegram.TOKEN_RE.match("123:abc-DEF_123")
    assert not telegram.TOKEN_RE.match("no-colon")
    assert telegram.CHAT_RE.match("-1001234") and telegram.CHAT_RE.match("42")
    assert not telegram.CHAT_RE.match("@channel")  # a channel name was never accepted


def test_write_env_is_owner_only_and_only_the_telegram_entries_go(store, tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"KEEP=me\nDROP=me\n{telegram.ENV_KEYS[1]}=-100\n", encoding="utf-8")
    lines = telegram.strip_env_keys(telegram.env_lines(env))  # only the Telegram key is removed
    telegram.write_env(env, lines, f"{telegram.ENV_KEYS[0]}=123:abc\n")
    assert env.read_text() == "KEEP=me\nDROP=me\nTG_BOT_TOKEN=123:abc\n"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600


def test_strip_env_keys_terminates_the_last_line_before_appending(store, tmp_path):
    env = tmp_path / ".env"
    env.write_text("KEEP=me", encoding="utf-8")  # no trailing newline
    kept = telegram.strip_env_keys(telegram.env_lines(env))
    telegram.write_env(env, kept, "NEW=1\n")
    assert env.read_text() == "KEEP=me\nNEW=1\n"


def test_a_token_in_env_is_migrated_into_the_store_and_dropped_from_the_file(store, tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"KEEP=me\n{telegram.ENV_KEYS[0]}=123:legacy\n", encoding="utf-8")
    assert telegram.migrate_env_token(env) is True
    assert telegram.token() == "123:legacy"
    assert env.read_text() == "KEEP=me\n"  # the secret is gone, the rest is intact
    assert telegram.migrate_env_token(env) is False  # idempotent: nothing left to move


def test_migration_never_overwrites_a_token_already_in_the_store(store, tmp_path):
    telegram.set_token("999:configured")
    env = tmp_path / ".env"
    env.write_text(f"{telegram.ENV_KEYS[0]}=123:legacy\n", encoding="utf-8")
    assert telegram.migrate_env_token(env) is False
    assert telegram.token() == "999:configured"
    assert env.exists()  # left alone rather than able to clobber the configured value


def test_a_malformed_env_token_is_not_migrated(store, tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"{telegram.ENV_KEYS[0]}=garbage\n", encoding="utf-8")
    assert telegram.migrate_env_token(env) is False
    assert telegram.token() == "" and env.exists()


def test_send_returns_false_and_never_raises_without_credentials(store, monkeypatch):
    monkeypatch.setenv(telegram.ENV_KEYS[0], "")
    monkeypatch.setenv(telegram.ENV_KEYS[1], "")
    assert telegram.send("hi") is False  # no token and no chat id


def test_send_survives_a_broken_transport(store, monkeypatch):
    telegram.set_token("123:abc")

    def boom(token, chat, text, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(telegram.telegram_kit, "send_message", boom)
    assert telegram.send("hi", "-1") is False  # a notification must never crash a job