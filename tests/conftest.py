import pytest

from monitask_center import config, i18n, workday


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    """Tmp HOME + MONITASK_CENTER_HOME; no test may touch the real user's files."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("MONITASK_CENTER_HOME", str(home / ".monitask-center"))
    runner = tmp_path / "run.sh"
    runner.write_text("#!/bin/bash\n")
    monkeypatch.setattr(config, "RUNNER", runner)
    i18n.catalog.cache_clear()
    workday.load_calendar.cache_clear()
    i18n.set_language(i18n.DEFAULT_LANGUAGE)
    return home
