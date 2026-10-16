"""Update check wiring: tag parsing, the opt-out toggle, the hint, the prompt answers, and `update`."""

from __future__ import annotations

import io
import json
import subprocess

import pytest

from monitask_center import cli, config, i18n, update_check
from monitask_center.tui import keyreader, menu, present

FOUND = update_check.UpdateAvailable(current="1.0.0", latest="v1.1.0")


def test_check_asks_the_public_github_releases_api(monkeypatch):
    asked = []

    def fetch(repo, timeout):
        asked.append(repo)
        return json.dumps({"tag_name": "v9.0.0"})

    monkeypatch.setattr(update_check, "fetch_github", fetch)
    started = cli._start_update_check(["status"])
    assert update_check.collect(started, timeout=5) == update_check.UpdateAvailable(
        current=cli.__version__, latest="v9.0.0")
    assert asked == ["weskao/monitask-center"]


def test_toggle_off_starts_nothing():
    config.save(config.Settings(update_check=False))
    assert cli._start_update_check([]) is None


def test_exec_commands_start_nothing():
    assert cli._start_update_check(["check"]) is None


def test_hint_goes_to_stderr_and_skips(capsys):
    assert cli._hint_update(FOUND) == update_check.SKIP
    err = capsys.readouterr().err
    assert "v1.1.0" in err and "git+https://github.com/weskao/monitask-center.git@v1.1.0" in err


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


# `monitask-center update`: always ends on the newest release tag. No test may install anything:
# every subprocess.run is recorded, and the release fetch is offline unless a test sets a tag.

TAG = "v1.6.3"


class _Runs(list):
    """Recorded commands, plus the exit codes a test queues for them (default 0)."""

    def __init__(self):
        super().__init__()
        self.codes: list[int] = []


@pytest.fixture(autouse=True)
def runs(monkeypatch):
    calls = _Runs()

    def fake_run(cmd, **_kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, calls.codes.pop(0) if calls.codes else 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(cli, "_direct_url", lambda: None)
    return calls


def _release(monkeypatch, tag=TAG, version="1.6.3", direct=None):
    monkeypatch.setattr(update_check, "fetch_github", lambda repo, timeout: json.dumps({"tag_name": tag}))
    monkeypatch.setattr(cli, "__version__", version)
    monkeypatch.setattr(cli, "_direct_url", lambda: direct)


@pytest.mark.parametrize("direct", [None, {"url": "https://github.com/weskao/monitask-center.git",
                                           "vcs_info": {"vcs": "git", "requested_revision": TAG}}])
def test_update_when_on_the_latest_tag_installs_nothing(monkeypatch, capsys, runs, direct):
    _release(monkeypatch, direct=direct)
    assert cli.main(["update"]) == 0
    assert runs == []
    assert capsys.readouterr().out.strip() == i18n.t("update.cmd.latest", current="1.6.3", latest=TAG)
    assert "1.6.3" in i18n.t("update.cmd.latest", current="1.6.3", latest=TAG)


def test_update_from_an_older_version_runs_the_update_now_steps_in_order(monkeypatch, capsys, runs):
    _release(monkeypatch, version="1.6.2")
    assert cli.main(["update"]) == 0
    steps = cli._upgrade(update_check.UpdateAvailable(current="1.6.2", latest=TAG))
    assert runs == steps
    out = capsys.readouterr().out
    assert i18n.t("update.cmd.updating", current="1.6.2", latest=TAG, command=" ".join(steps[0])) in out
    assert i18n.t("update.cmd.done", latest=TAG) in out


def test_update_dash_dash_form_is_the_same_command(monkeypatch, runs):
    _release(monkeypatch, version="1.6.2")
    assert cli.main(["--update"]) == 0
    assert len(runs) == 2


def test_update_from_a_local_checkout_reinstalls_and_says_the_checkout_is_untouched(monkeypatch, capsys,
                                                                                     runs, tmp_path):
    _release(monkeypatch, direct={"url": tmp_path.as_uri(), "dir_info": {"editable": True}})
    assert cli.main(["update"]) == 0
    assert len(runs) == 2
    assert i18n.t("update.cmd.local", path=tmp_path) in capsys.readouterr().out


def test_update_from_a_default_branch_install_reinstalls_the_tag(monkeypatch, runs):
    _release(monkeypatch, direct={"url": "https://github.com/weskao/monitask-center.git",
                                  "vcs_info": {"vcs": "git", "commit_id": "abc123"}})
    assert cli.main(["update"]) == 0
    assert runs[0][-2].endswith("@" + TAG)


def test_update_offline_exits_1_and_never_claims_up_to_date(capsys, runs):
    assert cli.main(["update"]) == 1  # conftest: fetch_github raises
    captured = capsys.readouterr()
    assert runs == [] and i18n.t("update.cmd.offline") in captured.err
    assert "✅" not in captured.out + captured.err


@pytest.mark.parametrize("failure", ["exit", "missing"])
def test_update_failure_exits_1_with_the_manual_command(monkeypatch, capsys, runs, failure):
    _release(monkeypatch, version="1.6.2")
    if failure == "exit":
        runs.codes.append(1)
    else:
        def no_uv(cmd, **_kw):
            runs.append(cmd)
            raise FileNotFoundError("uv")
        monkeypatch.setattr(subprocess, "run", no_uv)
    assert cli.main(["update"]) == 1
    assert len(runs) == 1  # the migrate step never runs after a failed install
    err = capsys.readouterr().err
    assert err.startswith("❌") and "git+https://github.com/weskao/monitask-center.git@v1.6.3" in err


@pytest.mark.parametrize("argv", [["update", "--help"], ["update", "-h"], ["update", "foo"],
                                  ["--update", "foo"], ["status", "--update"]])
def test_update_with_extra_args_exits_2_and_installs_nothing(monkeypatch, runs, argv):
    _release(monkeypatch, version="1.6.2")
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    assert exc.value.code == 2 and runs == []


def test_update_never_shows_the_after_command_prompt(monkeypatch):
    _release(monkeypatch, version="1.6.2")
    asked = []
    monkeypatch.setattr(cli, "_ask_update", lambda found: asked.append(found) or update_check.SKIP)
    monkeypatch.setattr(cli, "_hint_update", lambda found: asked.append(found) or update_check.SKIP)
    assert cli._start_update_check(["update"]) is None
    cli.main(["update"])
    assert asked == []


def test_update_strings_are_in_the_catalog_with_their_emoji():
    catalog = i18n.catalog("zh-TW")
    for key, emoji in (("update.cmd.latest", "✅"), ("update.cmd.updating", "⬆️"), ("update.cmd.done", "🎉"),
                       ("update.cmd.local", "ℹ️"), ("update.cmd.offline", "❌"), ("update.cmd.failed", "❌")):
        assert catalog[key].startswith(emoji), key
    assert "--update" in i18n.t("cli.help.update")
