import io
import json
import os
import re
import shutil
import subprocess
from collections import deque
from importlib.metadata import version

import pytest

from monitask_center import config, i18n, launchd, telegram
from monitask_center.i18n import t
from monitask_center.tui import config_menu, keyreader, logo, menu, present

WORDMARK = frozenset(logo.THICK + logo.BIG + logo.SMALL)


def _is_wordmark(line: str) -> bool:
    """A logo row behind its centring margin (blank lead, then exactly one tier row)."""
    return any(line.endswith(row) and not line[:-len(row)].strip() for row in WORDMARK)


def n(key: str) -> str:
    """The 1-based row number for a ROWS *key*, so a script never hardcodes an index.

    Adding or reordering a row would otherwise silently repoint a numbered script at the
    wrong setting.
    """
    return str(next(i for i, row in enumerate(menu.ROWS, 1) if row.key == key))

SGR = re.compile(r"\x1b\[[0-9;]*m")
REDRAW = re.compile(r"\x1b\[\d+A\r\x1b\[J")
CURSOR = re.compile(r"\x1b\[\?25[lh]")


class FakeTty(io.StringIO):
    def isatty(self):
        return True


REAL_SYNC = launchd.sync  # the autouse fixture below replaces it; one test wants the real one


@pytest.fixture(autouse=True)
def launchd_calls(monkeypatch):
    """No test reaches launchctl; sync calls are recorded."""
    calls = []
    monkeypatch.setattr(launchd, "is_installed", lambda: False)
    monkeypatch.setattr(launchd, "loaded", lambda: [])

    def sync(settings):
        calls.append(settings)
        return "not_installed", []

    monkeypatch.setattr(launchd, "sync", sync)
    return calls


def numbered(text, status_lines=None):
    out = io.StringIO()
    rc = config_menu(stdin=io.StringIO(text), stdout=out, status_lines=status_lines)
    return rc, out.getvalue()


def run_tui(monkeypatch, keys, size=(80, 24), status_lines=None):
    """Drive the arrow-key surface with scripted keys on a fake terminal."""
    queue = deque(keys)
    monkeypatch.setattr(keyreader, "read_key", lambda _stdin: queue.popleft() if queue else "quit")
    monkeypatch.setattr(keyreader, "read_edit_key", lambda _stdin: queue.popleft() if queue else "escape")
    monkeypatch.setattr(menu, "_terminal_size", lambda _out: os.terminal_size(size))
    out = FakeTty()
    rc = config_menu(stdin=FakeTty(), stdout=out, status_lines=status_lines)
    return rc, out.getvalue()


def frames(output):
    return [f.rstrip("\n").split("\n") for f in REDRAW.split(CURSOR.sub("", output))]


def status():
    return ["workday: yes (synthetic)", "next check 11:00", "monitask: timing", "last check 10:00 ok"]


@pytest.mark.parametrize("columns", [60, 80, 100])
@pytest.mark.parametrize("lines", [12, 24])
def test_the_panel_fits_the_terminal_it_draws_on(monkeypatch, columns, lines):
    config.save(replace_settings(vacation=("2026-10-05", "2026-10-06", "2026-10-07", "2026-12-25")))
    monkeypatch.setenv("NO_COLOR", "1")
    walk = ["down"] * 9 + ["right", "left"] + ["up"] * 2 + ["enter"]  # into vacation submenu
    walk += ["down", "down", "add", "2", "0", "x", "enter", "up", "esc", "reset", "n", "quit"]
    rc, output = run_tui(monkeypatch, walk, (columns, lines), status)
    assert rc == 0
    drawn = frames(output)
    assert len(drawn) > 15
    for frame in drawn:
        assert len(frame) < lines, frame
        for line in frame:
            assert present.width(line) <= columns, line
    # The static panel too, for every cursor position, with a long inline error showing.
    state = menu.State(config.load(), status)
    state.note = ("error", t("tui.error.vacation_date") * 3)
    out = FakeTty()
    for selected in range(len(menu.ROWS)):
        for panel in (menu._main_lines(state, selected, out),
                      menu._vacation_lines(state, min(selected, 4), out, editing="2026-10-05~")):
            assert len(panel) < lines
            assert all(present.width(line) <= columns for line in panel)
    assert all(present.width(line) <= columns for line in menu._main_lines(state, None, out))


def replace_settings(**kw):
    from dataclasses import replace

    return replace(config.Settings(), **kw)


# ── the Telegram rows: the token is a secret, the chat id is not ───────────────


def test_the_token_row_never_shows_the_token_and_a_saved_one_is_masked(no_real_keychain):
    no_real_keychain[telegram.TOKEN_KEY] = "1234567890:AAAABBBBCCCC"
    _, output = numbered("q\n")
    assert "1234567890" not in output and "AAAABBBB" not in output
    assert telegram.mask("1234567890:AAAABBBBCCCC") in output  # stars + the last 4 only


def test_a_saved_token_goes_to_the_credential_store_never_to_config(no_real_keychain, monkeypatch):
    monkeypatch.setattr(telegram, "read_token", lambda prompt: "123:typed_token")
    numbered(f"{n('telegram_token')}\n\nq\n")
    assert no_real_keychain.get(telegram.TOKEN_KEY) == "123:typed_token"
    on_disk = (config.config_path().read_text(encoding="utf-8")
               if config.config_path().exists() else "")
    assert "typed_token" not in on_disk


def test_a_malformed_token_is_refused_and_nothing_is_stored(no_real_keychain, monkeypatch):
    monkeypatch.setattr(telegram, "read_token", lambda prompt: "not-a-token")
    rc, output = numbered(f"{n('telegram_token')}\n\nq\n")
    assert no_real_keychain.get(telegram.TOKEN_KEY) is None  # refused, not half-saved
    assert t("tui.error.token_format") in output


def test_blank_keeps_the_existing_token(no_real_keychain, monkeypatch):
    no_real_keychain[telegram.TOKEN_KEY] = "123:already_here"
    monkeypatch.setattr(telegram, "read_token", lambda prompt: "")
    numbered(f"{n('telegram_token')}\n\nq\n")
    assert no_real_keychain.get(telegram.TOKEN_KEY) == "123:already_here"


def test_without_a_store_the_token_is_refused_with_a_pointer_to_the_env(no_real_keychain, monkeypatch):
    monkeypatch.setattr(telegram, "read_token", lambda prompt: "123:typed")
    monkeypatch.setattr(telegram, "set_token", lambda v: False)
    rc, output = numbered(f"{n('telegram_token')}\n\nq\n")
    assert no_real_keychain.get(telegram.TOKEN_KEY) is None
    assert t("tui.error.no_store", backend="test store") in output


def test_resetting_the_token_row_clears_the_store(monkeypatch, no_real_keychain):
    no_real_keychain[telegram.TOKEN_KEY] = "123:remove_me"
    rc, output = numbered(f"r{n('telegram_token')}\ny\nq\n")
    assert no_real_keychain.get(telegram.TOKEN_KEY) is None  # gone from the store
    assert t("tui.reset_done") in output


def test_the_chat_id_row_saves_to_config_json(no_real_keychain):
    numbered(f"{n('telegram_chat')}\n-1001234\nq\n")
    assert config.load().telegram_chat_id == "-1001234"  # an identifier, so plain JSON


def test_a_malformed_chat_id_is_refused_and_config_is_untouched():
    rc, output = numbered(f"{n('telegram_chat')}\n@channel\nq\n")
    assert t("tui.error.chat_format") in output
    assert not config.config_path().exists()


def test_the_secret_is_never_echoed_by_the_tui_either(monkeypatch, no_real_keychain):
    # Typing in the TUI draws one "*" per character, so the raw value cannot reach the screen.
    down = int(n("telegram_token")) - 1
    rc, output = run_tui(monkeypatch, ["down"] * down + ["enter", *"1", *"2", *"3",
                                                        "enter", "quit"])
    assert "123" not in output.replace("\x1b", "")  # masked as it was typed
    assert telegram.TOKEN_KEY not in no_real_keychain  # "123" is not a valid token


def test_non_tty_output_has_no_escape_bytes():
    rc, output = numbered("1\n9\n9\na 2026-10-05\n\nq\n", status)
    assert rc == 0
    assert "\x1b" not in output
    assert t("tui.title") in output and "workday: yes" in output


def test_config_menu_shows_the_installed_version():
    _, output = numbered("q\n")
    assert f"v{version('monitask-center')}" in output


def test_tty_with_no_color_redraws_but_never_paints(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    _, output = run_tui(monkeypatch, ["down", "right", "quit"])
    assert REDRAW.search(output)
    assert not SGR.search(output)


def test_tty_without_no_color_paints(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    _, output = run_tui(monkeypatch, ["down", "quit"])
    assert SGR.search(output)


def test_numbered_fallback_edits_and_saves_a_value():
    rc, output = numbered("1\n9\nq\n")
    assert rc == 0
    assert config.load().start_hour == 9
    assert json.loads(config.config_path().read_text())["start_hour"] == 9


def test_numbered_fallback_quits_on_eof():
    assert numbered("")[0] == 0


def test_invalid_window_is_rejected_inline_and_not_saved(launchd_calls):
    rc, output = numbered("1\n18\n2\n10\nq\n")
    assert rc == 0
    assert t("tui.error.hours") in output
    assert not config.config_path().exists()
    assert launchd_calls == []


def test_interval_is_clamped_to_the_window(monkeypatch):
    numbered("3\n99\nq\n")
    assert config.load().interval_hours == 8  # 18 - 10
    numbered("2\n12\nq\n")  # shrinking the window pulls the interval down with it
    s = config.load()
    assert (s.end_hour, s.interval_hours) == (12, 2)
    run_tui(monkeypatch, ["down", "down", "right", "right", "quit"])
    assert config.load().interval_hours == 2  # arrow keys stop at the ceiling


def test_vacation_add_range_remove_roundtrip():
    script = n("vacation") + "\na 2026-10-05~2026-10-07\na 2026/12/25\nd 2026-10-06\na 2026-13-01\nd 2027-01-01\nzz\n\nq\n"
    rc, output = numbered(script)
    assert rc == 0
    assert config.load().vacation == ("2026-10-05", "2026-10-07", "2026-12-25")
    assert t("tui.error.vacation_date") in output
    assert t("tui.vacation.missing", day="2027-01-01") in output
    assert t("tui.error.vacation_command") in output
    numbered(n("vacation") + "\na 2026-01-01~2027-06-01\n\nq\n")
    assert len(config.load().vacation) == 3  # over-long range rejected, nothing saved


def test_vacation_submenu_in_the_tui(monkeypatch):
    keys = ["up", "up", "enter", "add", *"2026-10-05~2026-10-06", "enter",
            "add", *"bad", "enter", "add", "backspace", "backspace", "backspace", *"2026-12-25",
            "enter", "down", "delete", "esc", "quit"]
    rc, output = run_tui(monkeypatch, keys)
    assert rc == 0
    assert config.load().vacation == ("2026-10-06", "2026-12-25")
    assert t("tui.error.vacation_date") in output


def test_sync_runs_only_for_schedule_changes(launchd_calls):
    # lunch, language, telegram, vacation: none of these move the launchd schedule
    script = f"4\n5\n{n('language')}\n6\n14\n7\n8\n{n('vacation')}\na 2026-10-05\n\n"
    _, output = numbered(script + "q\n")
    assert launchd_calls == []
    _, output = numbered("1\n9\n2\n17\n3\n2\nq\n")
    assert len(launchd_calls) == 3
    assert launchd.sync_message("not_installed", []) in output
    assert launchd_calls[-1].interval_hours == 2


def test_sync_failure_is_shown_not_raised(monkeypatch):
    def broken(_settings):
        raise OSError("synthetic launchctl failure")

    monkeypatch.setattr(launchd, "sync", broken)
    rc, output = numbered("1\n9\nq\n")
    assert rc == 1
    assert "synthetic launchctl failure" in output
    assert config.load().start_hour == 9


def test_reset_needs_confirmation_and_keeps_vacation():
    numbered(f"1\n9\n{n('vacation')}\na 2026-10-05\n\nr1\nn\nq\n")
    assert config.load().start_hour == 9
    numbered("r1\ny\nq\n")
    assert config.load().start_hour == 10
    numbered("1\n8\n4\nR\ny\nq\n")
    s = config.load()
    assert (s.start_hour, s.lunch_skip, s.vacation) == (10, True, ("2026-10-05",))


def test_tui_reset_row_with_confirm(monkeypatch):
    run_tui(monkeypatch, ["left", "left", "reset", "n", "quit"])
    assert config.load().start_hour == 8
    run_tui(monkeypatch, ["reset", "yes", "quit"])
    assert config.load().start_hour == 10


def test_read_only_row_changes_nothing():
    _, output = numbered(f"{n('install')}\nr{n('install')}\nq\n")
    assert t("tui.readonly") in output and t("tui.nothing_to_reset") in output
    assert not config.config_path().exists()


def test_broken_status_source_is_shown_not_raised():
    def boom():
        raise RuntimeError("synthetic probe failure")

    rc, output = numbered("q\n", boom)
    assert rc == 0 and "synthetic probe failure" in output


def test_switching_language_rerenders_immediately(monkeypatch, tmp_path):
    locales = tmp_path / "locales"
    shutil.copytree(i18n.LOCALES_DIR, locales)
    (locales / "en-XA").mkdir()
    (locales / "en-XA" / "tui.json").write_text(json.dumps({"tui.title": "PSEUDO TITLE"}))
    monkeypatch.setattr(i18n, "LOCALES_DIR", locales)
    i18n.catalog.cache_clear()
    rc, output = numbered(f"{n('language')}\nq\n")
    assert rc == 0
    assert config.load().language == "en-XA"
    first, _, after = output.partition("PSEUDO TITLE")
    assert t("tui.title", "zh-TW") in first and after  # old title before, new title right after


def test_no_cjk_literal_in_tui_code():
    src = os.path.dirname(menu.__file__)
    for name in os.listdir(src):
        if name.endswith(".py"):
            text = open(os.path.join(src, name), encoding="utf-8").read()
            assert not re.search(r"[\u3000-\u9fff\uff00-\uffef]", text), name


def test_tui_defers_sync_until_the_cursor_leaves_the_row(monkeypatch, launchd_calls):
    run_tui(monkeypatch, ["right", "right", "right"])  # then the scripted keys run out -> quit
    assert len(launchd_calls) == 1 and launchd_calls[0].start_hour == 13
    _, output = run_tui(monkeypatch, ["left", "left", "down", "quit"])
    assert len(launchd_calls) == 2 and launchd_calls[1].start_hour == 11
    assert launchd.sync_message("not_installed", []) in output
    run_tui(monkeypatch, ["down", "down", "down", "right", "down", "right", "quit"])  # lunch rows
    assert len(launchd_calls) == 2


def test_ctrl_c_still_syncs_and_restores_the_cursor(monkeypatch, launchd_calls):
    def interrupt(_stdin):
        raise KeyboardInterrupt

    keys = iter(["right"])
    monkeypatch.setattr(keyreader, "read_key", lambda s: next(keys, None) or interrupt(s))
    out = FakeTty()
    assert config_menu(stdin=FakeTty(), stdout=out) == 0
    assert len(launchd_calls) == 1
    assert out.getvalue().startswith("\x1b[?25l") and out.getvalue().endswith("\x1b[?25h")


def test_redraw_walks_up_exactly_the_previous_frame(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    _, output = run_tui(monkeypatch, ["down", "down", "quit"], status_lines=status)
    ups = [int(n) for n in re.findall(r"\x1b\[(\d+)A", output)]
    drawn = frames(output)
    assert ups == [len(frame) for frame in drawn[:-1]]


def test_tui_reset_all_needs_confirmation(monkeypatch):
    run_tui(monkeypatch, ["right", "reset_all", "n", "quit"])
    assert config.load().start_hour == 11
    run_tui(monkeypatch, ["reset_all", "yes", "quit"])
    assert config.load().start_hour == 10
    _, output = numbered("1\n9\nR\nn\nq\n")
    assert config.load().start_hour == 9 and t("tui.cancelled") in output


def test_arrow_keys_stop_at_the_interval_ceiling_without_an_error(monkeypatch):
    config.save(replace_settings(start_hour=10, end_hour=12, interval_hours=2))
    _, output = run_tui(monkeypatch, ["down", "down", "right", "quit"])
    assert config.load().interval_hours == 2
    assert t("tui.error.interval") not in output and t("tui.unchanged") in output


def test_rejected_vacation_text_is_offered_again(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    _, output = run_tui(monkeypatch, ["up", "up", "enter", "add", *"bad", "enter", "add", "enter", "esc", "quit"])
    prompts = re.findall(re.escape(t("tui.vacation.prompt")) + r"(\S*)", output)
    assert "bad" + menu.CARET in prompts[-1:]  # reopened with the rejected text


def test_unicode_digit_choice_is_an_error_not_a_crash():
    rc, output = numbered("²\nq\n")
    assert rc == 0 and t("tui.error.choice") in output


def test_installed_and_loaded_status_is_shown(monkeypatch):
    monkeypatch.setattr(launchd, "is_installed", lambda: True)
    monkeypatch.setattr(launchd, "loaded", lambda: ["123\t0\tcom.monitask-center.check"])
    _, output = numbered("q\n")
    assert t("tui.install.loaded") in output


class _Pipe:
    def __init__(self, data: bytes, close: bool = True):
        self.r, w = os.pipe()
        os.write(w, data)
        if close:
            os.close(w)
        else:
            self.w = w

    def fileno(self):
        return self.r


@pytest.mark.parametrize(("data", "key"), [
    (b"\x1b[A", "up"), (b"\x1b[B", "down"), (b"\x1bOC", "right"), (b"\x1b[3~", "delete"),
    (b"\r", "enter"), (b"R", "reset_all"), (b"r", "reset"), (b"", "quit"), (b"z", "other"),
])
def test_read_key_parses_real_bytes(data, key):
    assert keyreader.read_key(_Pipe(data)) == key


def test_bare_escape_does_not_wait_for_more_bytes():
    pipe = _Pipe(b"\x1b", close=False)  # writer still open: a blocking read would hang
    assert keyreader.read_key(pipe) == "esc"
    os.close(pipe.w)


def test_read_edit_key_returns_whole_characters():
    assert keyreader.read_edit_key(_Pipe("\u958b".encode())) == "\u958b"
    assert keyreader.read_edit_key(_Pipe(b"\x7f")) == "backspace"
    assert keyreader.read_edit_key(_Pipe(b"\x1b[A")) == ""
    assert keyreader.read_edit_key(_Pipe(b"")) == "escape"


def test_failing_bootstrap_keeps_frame_height_and_prints_no_launchctl_text(monkeypatch, capfd):
    """Real launchd.sync -> real _launchctl, with subprocess.run stubbed (never the real launchctl)."""
    stderr = "Bootstrap failed: 5: Input/output error\nSECOND-LINE-LEAK\n"

    def fake_run(cmd, **kw):
        assert cmd[0] == "launchctl"
        failed = cmd[1] == "bootstrap"
        assert not failed or kw.get("stderr") == subprocess.PIPE  # never inherited
        return subprocess.CompletedProcess(cmd, 5 if failed else 0, "", stderr if failed else "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(launchd, "sync", REAL_SYNC)
    monkeypatch.setattr(launchd, "is_installed", lambda: True)
    monkeypatch.setenv("NO_COLOR", "1")
    rc, output = run_tui(monkeypatch, ["right", "down", "up", "quit"], (80, 24), status)
    assert rc == 1
    drawn = frames(output)
    shown = menu.t("tui.error.sync", error=launchd.describe_error(
        launchd.LaunchdError(5, ["launchctl"], "Bootstrap failed: 5: Input/output error")))
    assert any(shown in "\n".join(f) for f in drawn)  # the localized note was drawn
    assert len({len(f) for f in drawn}) == 1, [len(f) for f in drawn]  # frame never changed height
    assert "SECOND-LINE-LEAK" not in output and "non-zero" not in output
    assert "['launchctl" not in output and "CalledProcessError" not in output
    assert capfd.readouterr() == ("", "")
    assert not launchd.plist_path("check").exists()  # rolled back


def test_corrupt_config_is_backed_up_and_noted_on_the_first_draw(monkeypatch):
    path = config.config_path()
    path.parent.mkdir(parents=True)
    path.write_text('{"start_hour": 9,}')
    rc, output = run_tui(monkeypatch, ["quit"])
    first = "\n".join(frames(output)[0])
    assert "config.json.corrupt" in first and "tui.corrupt_backup" not in first
    assert path.with_name("config.json.corrupt").read_text() == '{"start_hour": 9,}'
    rc, output = numbered("1\n9\nq\n")  # a later save keeps the backup
    assert config.load().start_hour == 9
    assert path.with_name("config.json.corrupt").read_text() == '{"start_hour": 9,}'


def test_numbered_fallback_also_shows_the_corrupt_note():
    path = config.config_path()
    path.parent.mkdir(parents=True)
    path.write_text("not json")
    _, output = numbered("q\n")
    assert "config.json.corrupt" in output and "tui.corrupt_backup" not in output


def _panel(monkeypatch, columns, lines, color):
    if color:
        monkeypatch.delenv("NO_COLOR", raising=False)
    else:
        monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr(menu, "_terminal_size", lambda _out: os.terminal_size((columns, lines)))
    state = menu.State(config.load(), lambda: ["monitask: timing", "last check 10:00 ok",
                                               "next check 11:00", "workday: yes"])
    return [menu._main_lines(state, i, FakeTty()) for i in range(len(menu.ROWS))]


@pytest.mark.parametrize("color", [False, True])
@pytest.mark.parametrize("columns", [40, 46, 60, 63, 80, 100, 140])
@pytest.mark.parametrize("lines", [12, 24, 30, 40])
def test_logo_panel_never_overflows_in_any_size(monkeypatch, columns, lines, color):
    for panel in _panel(monkeypatch, columns, lines, color):
        assert len(panel) < lines
        assert all(present.width(line) <= columns - 1 for line in panel)
        wordmark = [line for line in panel if _is_wordmark(present.strip_ansi(line))]
        assert len(wordmark) in (0, len(logo.SMALL), len(logo.BIG), len(logo.THICK))  # atomic: never a partial wordmark


def test_the_logo_shows_at_80x26_with_two_status_lines_and_at_100x30_with_everything(monkeypatch):
    # 18 setting rows: logo + both status lines now needs 32 rows.
    plain = [present.strip_ansi(line) for line in _panel(monkeypatch, 80, 32, False)[0]]
    assert " " * logo.indent(80, logo.SMALL) + logo.SMALL[0] in plain  # the 3-row tier keeps both status lines
    assert t("tui.tagline") not in "\n".join(plain)
    assert any("monitask: timing" in line for line in plain)
    assert any("last check 10:00" in line for line in plain)
    big = [present.strip_ansi(line) for line in _panel(monkeypatch, 100, 36, False)[0]]
    assert any(t("tui.tagline") in line for line in big)
    assert sum(1 for line in big if any(k in line for k in ("monitask: t", "last check", "next check",
                                                              "workday: yes"))) >= 3


def test_numbered_fallback_with_a_tty_stdout_emits_no_escape_byte(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    config.save(replace_settings(vacation=("2026-10-05",)))
    out = FakeTty()  # stdout is a terminal, stdin is not: the numbered surface
    rc = config_menu(stdin=io.StringIO(f"{n('vacation')}\n9\n\nq\n"), stdout=out, status_lines=status)
    assert rc == 0 and "\x1b" not in out.getvalue()
    # The numbered surface has no terminal size, so it falls back to 80x24 -- narrow enough
    # that BIG (needs 92 columns) does not fit. Assert a whole tier is present, not which.
    assert any(row in out.getvalue() for tier in (logo.THICK, logo.BIG, logo.SMALL) for row in tier)
    assert "2026-10-05" in out.getvalue()  # the vacation submenu was rendered too


def test_non_utf8_stdout_replaces_glyphs_instead_of_crashing():
    raw = io.BytesIO()
    out = io.TextIOWrapper(raw, encoding="latin-1")  # e.g. LANG=en_US.ISO8859-1
    rc = config_menu(stdin=io.StringIO("q\n"), stdout=out, status_lines=status)
    out.flush()
    assert rc == 0 and b"?" in raw.getvalue()


def test_interactive_surface_keeps_colour_and_no_color_still_wins(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    _, coloured = run_tui(monkeypatch, [], status_lines=status)
    assert SGR.search(coloured)
    monkeypatch.setenv("NO_COLOR", "1")
    _, plain = run_tui(monkeypatch, [], status_lines=status)
    assert not SGR.search(plain)


@pytest.mark.parametrize("columns,lines,logo_shown", [
    # 18 setting rows now, so the status block needs 28 rows and the logo 30. Notes drop
    # before rows (present.compose), so below 28 the status line is what goes, not a row.
    (63, 28, False), (63, 30, True), (46, 28, False), (46, 30, True), (45, 28, False), (45, 30, False),
])
def test_monitask_status_line_outranks_the_logo_on_short_terminals(monkeypatch, columns, lines, logo_shown):
    for panel in _panel(monkeypatch, columns, lines, False):
        plain = [present.strip_ansi(line) for line in panel]
        assert any("monitask: timing" in line for line in plain)
        assert any(_is_wordmark(line) for line in plain) == logo_shown
        assert len(panel) < lines


def test_frame_height_is_constant_while_the_cursor_moves_with_status_first(monkeypatch):
    for size in ((63, 20), (63, 22), (46, 21), (80, 24), (100, 30)):
        heights = {len(p) for p in _panel(monkeypatch, *size, False)}
        assert len(heights) == 1, size


def test_numbered_fallback_prints_the_logo_as_plain_glyphs():
    _, output = numbered("q\n", status)
    shown = any(row in output for tier in (logo.THICK, logo.BIG, logo.SMALL) for row in tier)
    assert shown and "\x1b" not in output and t("tui.tagline") in output


def test_screen_clears_from_the_top_after_a_resize_and_walks_up_otherwise(monkeypatch):
    size = [(100, 30)]
    monkeypatch.setattr(menu, "_terminal_size", lambda _out: os.terminal_size(size[0]))
    out = FakeTty()
    screen = menu._Screen(out)
    screen.draw(["a", "b", "c"])
    assert out.getvalue() == "a\nb\nc\n"
    screen.draw(["a", "b", "c"])
    assert out.getvalue().count("\x1b[3A\r\x1b[J") == 1 and "\x1b[H" not in out.getvalue()
    size[0] = (60, 20)  # resized
    screen.draw(["a", "b", "c"])
    assert out.getvalue().count("\x1b[H\x1b[2J") == 1
    assert out.getvalue().count("A\r\x1b[J") == 1  # no new cursor-up for this frame
    screen.draw(["a", "b", "c"])  # settled: back to cursor-up
    assert out.getvalue().count("\x1b[3A\r\x1b[J") == 2 and out.getvalue().count("\x1b[2J") == 1
    size[0] = (60, 24)  # height alone also counts
    screen.draw(["a"])
    assert out.getvalue().count("\x1b[2J") == 2


def test_sigwinch_wakes_a_blocked_key_wait_without_eating_a_key():
    import signal
    import threading

    r, w = os.pipe()
    previous = signal.signal(signal.SIGWINCH, menu._on_winch)
    timer = threading.Timer(0.2, signal.pthread_kill,
                            (threading.main_thread().ident, signal.SIGWINCH))
    try:
        with os.fdopen(r) as stdin:
            timer.start()
            assert menu._wait(stdin) is True  # woken by the signal, not by input
            os.write(w, b"x")
            assert menu._wait(stdin) is False  # a real key is ready
            assert os.read(r, 1) == b"x"  # and was not consumed
    finally:
        timer.cancel()
        signal.signal(signal.SIGWINCH, previous)
        os.close(w)


def test_a_key_wait_with_a_timeout_wakes_for_the_spinner_without_eating_a_key():
    r, w = os.pipe()
    try:
        with os.fdopen(r) as stdin:
            assert menu._wait(stdin, 0.01) is True  # nothing typed: time to redraw
            os.write(w, b"x")
            assert menu._wait(stdin, 0.01) is False
            assert os.read(r, 1) == b"x"
    finally:
        os.close(w)


def test_tui_installs_the_sigwinch_redraw_and_restores_the_previous_handler(monkeypatch):
    """A resize with no keypress must redraw: the real _tui path installs the handler."""
    import contextlib
    import signal
    import threading

    r, w = os.pipe()
    sentinel = lambda *_: None  # noqa: E731
    before = signal.signal(signal.SIGWINCH, sentinel)
    out = FakeTty()
    keys_read = []

    class PipeTty:
        def isatty(self):
            return True

        def fileno(self):
            return r

    def read_key(_stdin):
        keys_read.append(out.getvalue().count("\u2501\u2501 \u25c6"))  # frames drawn when the key arrives
        return "quit"

    monkeypatch.setattr(keyreader, "key_session", lambda _stdin: contextlib.nullcontext())
    monkeypatch.setattr(keyreader, "read_key", read_key)
    monkeypatch.setattr(menu, "_terminal_size", lambda _out: os.terminal_size((80, 24)))
    main = threading.main_thread().ident
    timers = [threading.Timer(0.2, signal.pthread_kill, (main, signal.SIGWINCH)),
              threading.Timer(0.6, os.write, (w, b"x"))]
    try:
        for timer in timers:
            timer.start()
        assert config_menu(stdin=PipeTty(), stdout=out, status_lines=status) == 0
        assert keys_read == [2]  # first frame, then the SIGWINCH redraw, and only then the key
        assert signal.getsignal(signal.SIGWINCH) is sentinel  # previous handler restored on exit
    finally:
        for timer in timers:
            timer.cancel()
        signal.signal(signal.SIGWINCH, before)
        os.close(r)
        os.close(w)


@pytest.mark.parametrize("pointer", ["ascii", "emoji"])
def test_tui_uses_the_chosen_pointer_once_and_the_frame_still_fits(monkeypatch, pointer):
    monkeypatch.setenv("MONITASK_CENTER_POINTER", pointer)
    monkeypatch.setenv("NO_COLOR", "1")
    mark = present.POINT_EMOJI if pointer == "emoji" else present.POINT_ASCII
    rc, output = run_tui(monkeypatch, ["down", "down", "up"], (60, 20), status)
    assert rc == 0
    for frame in frames(output):
        assert len(frame) < 20 and all(present.width(ln) <= 59 for ln in frame)
        assert sum(ln.startswith(mark) for ln in frame) == 1
        assert not any(ln.startswith(present.POINT_ASCII if pointer == "emoji" else present.POINT_EMOJI)
                       for ln in frame)


def test_numbered_fallback_never_shows_a_pointer(monkeypatch):
    monkeypatch.setenv("MONITASK_CENTER_POINTER", "emoji")
    _, output = numbered("q\n", status)
    assert present.POINT_EMOJI not in output and "\n> " not in output


@pytest.mark.parametrize("selected", [None, 0])
def test_vacation_menu_shows_absolute_storage_path(monkeypatch, selected):
    monkeypatch.setenv("MONITASK_CENTER_HOME", "relative-settings")
    monkeypatch.setattr(menu, "_terminal_size", lambda _out: os.terminal_size((200, 24)))
    output = "\n".join(menu._vacation_lines(menu.State(config.Settings()), selected, io.StringIO()))
    assert str(config.config_path().resolve()) in output


def test_overtime_time_steps_by_half_hours_and_stops_at_both_ends():
    row = next(r for r in menu.ROWS if r.key == "overtime_time")
    s = config.Settings()
    assert menu._value(menu.State(s), row) == "18:30"
    assert config.clock(menu._step(s, row, 1).overtime_minutes) == "19:00"
    assert config.clock(menu._step(s, row, -1).overtime_minutes) == "18:00"
    earliest = replace_settings(overtime_minutes=config.OVERTIME_EARLIEST)
    assert menu._step(earliest, row, -1) == earliest
    latest = replace_settings(overtime_minutes=config.OVERTIME_LATEST)
    assert menu._step(latest, row, 1) == latest


def test_overtime_arrow_edit_saves_and_resyncs_the_schedule(monkeypatch):
    synced = []
    monkeypatch.setattr(launchd, "sync", lambda s: synced.append(s.overtime_minutes) or ("reinstalled", ["overtime"]))
    index = [r.key for r in menu.ROWS].index("overtime_time")
    run_tui(monkeypatch, ["down"] * index + ["right", "right", "quit"])
    assert config.load().overtime_minutes == 19 * 60 + 30 and synced == [19 * 60 + 30]


def test_overtime_toggle_needs_no_launchd_sync(monkeypatch):
    monkeypatch.setattr(launchd, "sync", lambda s: pytest.fail("the job is always scheduled"))
    _, output = numbered(f"{n('overtime_prompt')}\nq\n")
    assert config.load().overtime_prompt is False


def test_overtime_numbered_entry_accepts_only_valid_clock_times():
    _, output = numbered(f"{n('overtime_time')}\n18:45\nq\n")
    assert t("tui.error.clock", low="18:00", high="20:30") in output
    assert config.load().overtime_minutes == 18 * 60 + 30
    numbered(f"{n('overtime_time')}\n20:30\nq\n")
    assert config.load().overtime_minutes == 20 * 60 + 30


@pytest.mark.parametrize("key,field,shown,stepped,wrapped", [
    ("activity_alert", "activity_alert_percent", "60%", 65, (90, 10)),
    ("activity_minutes", "activity_check_minutes", t("tui.value.minutes", n=10), 20, (60, 10)),
])
def test_activity_alert_rows_step_through_fixed_choices(key, field, shown, stepped, wrapped):
    row = next(r for r in menu.ROWS if r.key == key)
    s = config.Settings()
    assert menu._value(menu.State(s), row) == shown
    assert getattr(menu._step(s, row, 1), field) == stepped
    last, first = wrapped
    assert getattr(menu._step(replace_settings(**{field: last}), row, 1), field) == first


def test_activity_alert_numbered_rows_step_without_typing():
    _, output = numbered(f"{n('activity_alert')}\n{n('activity_minutes')}\nq\n")
    assert (config.load().activity_alert_percent, config.load().activity_check_minutes) == (65, 20)
