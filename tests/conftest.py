import pytest

from monitask_center import cli, config, i18n, telegram, workday

# Captured before any fixture replaces them, so a test that wants to exercise the real
# credential-store logic can restore it (see the `store` fixture in test_telegram.py).
for _name in ("available", "backend_label", "token", "set_token", "send"):
    setattr(telegram, f"_real_{_name}", getattr(telegram, _name))


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    """Tmp HOME + MONITASK_CENTER_HOME; no test may touch the real user's files."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("MONITASK_CENTER_HOME", str(home / ".monitask-center"))
    for var in telegram.ENV_KEYS:  # a real token in the developer's env must not decide a test
        monkeypatch.delenv(var, raising=False)
    runner = tmp_path / "run.sh"
    runner.write_text("#!/bin/bash\n")
    monkeypatch.setattr(config, "RUNNER", runner)
    i18n.catalog.cache_clear()
    workday.load_calendar.cache_clear()
    i18n.set_language(i18n.DEFAULT_LANGUAGE)
    return home


@pytest.fixture(autouse=True)
def no_update_network(monkeypatch):
    """cli.main starts a background release check; no test may reach the real remote."""
    def offline(_dist, _timeout):
        raise OSError("offline in tests")

    monkeypatch.setattr(cli, "_fetch_git_tags", offline)


@pytest.fixture(autouse=True)
def no_real_keychain(monkeypatch):
    """An in-memory stand-in for the OS credential store, so no test prompts for or
    writes the real Keychain. Tests that care about the token set it via ``store``."""
    store: dict[str, str] = {}
    monkeypatch.setattr(telegram, "available", lambda: True)
    monkeypatch.setattr(telegram, "backend_label", lambda: "test store")
    monkeypatch.setattr(telegram, "token", lambda: store.get(telegram.TOKEN_KEY, ""))
    monkeypatch.setattr(telegram, "set_token", lambda v: _put(store, v))
    monkeypatch.setattr(telegram, "send", lambda text, chat_id="": False)
    return store


def _put(store: dict[str, str], value: str) -> bool:
    if not value:
        store.pop(telegram.TOKEN_KEY, None)
    else:
        store[telegram.TOKEN_KEY] = value
    return True
