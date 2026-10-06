"""Update check wiring: tag parsing, the opt-out toggle, the hint, and the prompt answers."""

from __future__ import annotations

import io
import json
import subprocess

from monitask_center import cli, config, i18n, update_check
from monitask_center.tui import keyreader, menu, present

FOUND = update_check.UpdateAvailable(current="1.0.0", latest="v1.1.0")


def _tags(monkeypatch, stdout):
    done = subprocess.CompletedProcess([], 0, stdout=stdout)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: done)


def test_newest_tag_wins_numerically(monkeypatch):
    monkeypatch.undo()  # the autouse fixture stubs _fetch_git_tags; this test wants the real one
    _tags(monkeypatch, "a\trefs/tags/v0.9.0\nb\trefs/tags/v0.10.0\nc\trefs/tags/notes\n")
    assert json.loads(cli._fetch_git_tags("x", 1))["tag_name"] == "v0.10.0"


def test_offline_git_is_the_offline_error_family(monkeypatch):
    monkeypatch.undo()

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("git", 1)

    monkeypatch.setattr(subprocess, "run", boom)
    try:
        cli._fetch_git_tags("x", 1)
    except OSError:
        return
    raise AssertionError("expected OSError")


def test_toggle_off_starts_nothing():
    config.save(config.Settings(update_check=False))
    assert cli._start_update_check([]) is None


def test_exec_commands_start_nothing():
    assert cli._start_update_check(["check"]) is None


def test_hint_goes_to_stderr_and_skips(capsys):
    assert cli._hint_update(FOUND) == update_check.SKIP
    err = capsys.readouterr().err
    assert "v1.1.0" in err and "git pull" in err


def _prompt(monkeypatch, keys):
    seq = iter(keys)
    monkeypatch.setattr(keyreader, "read_key", lambda _stdin: next(seq))
    monkeypatch.setattr(keyreader, "key_session", lambda _stdin: __import__("contextlib").nullcontext())
    out = io.StringIO()
    return menu.update_prompt(FOUND, "https://example.com/r", stdin=io.StringIO(), out=out), out.getvalue()


def test_prompt_answers(monkeypatch):
    assert _prompt(monkeypatch, ["enter"])[0] == update_check.UPDATE_NOW
    assert _prompt(monkeypatch, ["down", "enter"])[0] == update_check.SKIP
    assert _prompt(monkeypatch, ["up", "enter"])[0] == update_check.SKIP_VERSION
    assert _prompt(monkeypatch, ["quit"])[0] == update_check.SKIP


def test_prompt_renders_title_and_rows(monkeypatch):
    _, shown = _prompt(monkeypatch, ["quit"])
    plain = present.strip_ansi(shown)
    assert i18n.t("update.available", latest="v1.1.0", current="1.0.0") in plain
    assert i18n.t("update.skip_version") in plain
