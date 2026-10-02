import re

import pytest

from monitask_center.tui import present

CJK = "\u958b\u59cb\u6642\u9593"  # four East-Asian Wide glyphs = 8 columns
SGR = re.compile(r"\x1b\[[0-9;]*m")


def test_width_counts_wide_glyphs_twice_and_ignores_ansi_and_combining():
    assert present.width("abc") == 3
    assert present.width(CJK) == 8
    assert present.width("\x1b[38;5;214mab\x1b[0m") == 2
    assert present.width("e\u0301") == 1


@pytest.mark.parametrize("columns", range(1, 12))
def test_fit_never_exceeds_the_budget_and_marks_the_cut(columns):
    text = CJK + "abc"
    fitted = present.fit(text, columns)
    assert present.width(fitted) <= columns
    if present.width(text) > columns:
        assert fitted.endswith(present.ELLIPSIS)
    else:
        assert fitted == text


def test_fit_with_no_room_is_empty():
    assert present.fit(CJK, 0) == ""


def test_frame_rows_counts_wrapped_physical_rows():
    assert present.frame_rows(["a" * 10, CJK * 2, ""], 10) == 1 + 2 + 1


def _compose(columns, lines, color=False, selected=0, pointer=present.POINT_ASCII, **kw):
    items = [(CJK * (i % 3 + 1), CJK + str(i), "value") for i in range(10)]
    return present.compose(
        "title " + CJK, items, selected, columns=columns, lines=lines,
        style=present.Style(color, pointer), hint="hint " + CJK * 3, hint2="hint2",
        message=("error", CJK * 40), help_text="help " + CJK * 30,
        extra=["status " + CJK * 30, "second"], extra_title=CJK, **kw,
    )


POINTERS = [present.POINT_ASCII, present.POINT_EMOJI]


@pytest.mark.parametrize("pointer", POINTERS)
@pytest.mark.parametrize("columns", [40, 60, 80, 100])
@pytest.mark.parametrize("lines", [8, 12, 24, None])
def test_frame_fits_width_and_height(columns, lines, pointer):
    for selected in range(10):
        out = _compose(columns, lines, selected=selected, pointer=pointer)
        assert all(present.width(line) <= columns - 1 for line in out)
        if lines is not None:
            assert len(out) < lines
        cursor = [line for line in out if line.startswith(pointer)]
        assert len(cursor) == 1 and str(selected) in cursor[0]


def test_frame_height_is_constant_while_the_cursor_moves():
    assert len({len(_compose(60, 14, selected=i)) for i in range(10)}) == 1


def test_short_terminal_drops_notes_before_rows():
    roomy = _compose(80, 30)
    assert any("status" in line for line in roomy) and any("help" in line for line in roomy)
    tight = _compose(80, 16)  # 15 lines: 4 fixed + all 10 rows + 1 note
    assert len(tight) == 15 and "/10" not in tight[0]  # no viewport: every row still shown
    assert any("help" in line for line in tight)  # help is the last note to go
    assert not any("status" in line for line in tight)


def test_viewport_title_shows_position_when_rows_are_cut():
    out = _compose(80, 8, selected=7)
    assert "8/10" in out[0]


def test_colour_is_all_or_nothing():
    assert not any("\x1b" in line for line in _compose(80, 24, color=False))
    painted = "\n".join(_compose(80, 24, color=True))
    assert SGR.search(painted)
    assert all(present.width(line) <= 79 for line in _compose(80, 24, color=True))


def test_color_enabled_needs_a_tty_and_no_NO_COLOR(monkeypatch):
    class Tty:
        def isatty(self):
            return True

    monkeypatch.delenv("NO_COLOR", raising=False)
    assert present.color_enabled(Tty())
    assert not present.color_enabled(object())
    monkeypatch.setenv("NO_COLOR", "1")
    assert not present.color_enabled(Tty())


def test_control_characters_in_notes_cannot_break_the_frame():
    out = present.compose("t", [("a", "b", "value")], 0, columns=40, lines=None,
                          style=present.Style(False), hint="h", message=("error", "x\ny\rz"))
    assert len(out) == len("\n".join(out).split("\n"))


def test_fullwidth_forms_count_two_columns():
    assert present.width("\uff21\uff1a") == 4  # FULLWIDTH A + FULLWIDTH COLON


def _with_logo(lines, status=4):
    from monitask_center.tui import logo

    return present.compose(
        "title", [("row", "value", "value")] * 10, 0, columns=100, lines=lines,
        style=present.Style(False), hint="hint", hint2="hint2", help_text="help",
        extra=[f"status{i}" for i in range(status)], extra_title="st",
        logo=logo.lines(100, False), tagline="tagline",
    )


def test_logo_appears_whole_exactly_when_it_fits():
    from monitask_center.tui import logo

    first = logo.lines(100, False)[0]
    # 4 fixed + 1 + 10 rows + help + second hint + heading + first status + 4 logo rows = 23
    for lines, shown in ((22, False), (23, True), (24, True)):
        out = _with_logo(lines)
        assert (first in out) == shown, lines
        assert all(row in out for row in logo.lines(100, False)) == shown  # never half drawn
        assert len(out) < lines
    assert not any("tagline" in line for line in _with_logo(22))  # tagline never without logo
    assert any(present.THIN * 5 in line for line in _with_logo(22))  # skipped, not a cut-off: the rest stays


def test_priority_status_heading_and_first_line_beat_logo_which_beats_rule_and_rest():
    text = "\n".join(_with_logo(22))  # logo skipped whole, the Monitask line survives
    assert "help" in text and "hint2" in text and "status0" in text and "status1" in text
    def bare_rules(out):
        return [ln for ln in out if set(ln.strip()) == {present.THIN}]

    out = _with_logo(23)  # exactly help + hint2 + heading/status0 + logo fit
    text = "\n".join(out)
    assert "status0" in text and "status1" not in text and not bare_rules(out)
    out = _with_logo(25)  # + rule, second status line; tagline waits
    text = "\n".join(out)
    assert bare_rules(out) and "status1" in text and "status2" not in text and "tagline" not in text


@pytest.mark.parametrize("lines", range(12, 40))
def test_first_status_line_is_visible_whenever_help_and_hints_are(lines):
    out = _with_logo(lines)
    text = "\n".join(out)
    if "hint2" in text and lines >= 20:
        assert "status0" in text
    assert len(out) <= lines


@pytest.mark.parametrize("lines", range(12, 40))
def test_status_rule_never_drawn_without_the_status_heading(lines):
    out = _with_logo(lines)
    if any(set(ln.strip()) == {present.THIN} for ln in out):  # the first block that misses ends the list
        assert "status0" in "\n".join(out)


def test_tall_terminal_shows_tagline_and_every_status_line():
    text = "\n".join(_with_logo(30))
    assert "tagline" in text and all(f"status{i}" in text for i in range(4))


def test_tagline_never_drawn_without_the_logo():
    from monitask_center.tui import logo

    # 4 fixed + 1 + 10 rows + help + hint2 = 17: two spare rows fit rule + tagline, not the logo
    out = _with_logo(19, status=0)
    assert "tagline" not in "\n".join(out) and logo.lines(100, False)[0] not in out
    out = _with_logo(23, status=0)  # logo (4) + rule + tagline fit
    assert "tagline" in "\n".join(out) and logo.lines(100, False)[0] in out


def test_both_pointer_styles_are_two_columns_and_keep_the_columns_aligned():
    assert {present.width(p) for p in POINTERS} == {2}
    ascii_rows, emoji_rows = (_compose(80, None, selected=3, pointer=p) for p in POINTERS)
    assert len(ascii_rows) == len(emoji_rows)
    differing = [(a, e) for a, e in zip(ascii_rows, emoji_rows) if a != e]
    assert len(differing) == 1  # only the cursor row differs
    a, e = differing[0]
    assert a.startswith("> ") and e.startswith(present.POINT_EMOJI)
    assert a[2:] == e[1:] and present.width(a) == present.width(e)  # same text, same columns
    for rows in (ascii_rows, emoji_rows):  # the cursor row's value starts where every other row's does
        cols = {present.width(ln[:ln.index(CJK + str(i))]) for i in range(10)
                for ln in rows if ln.rstrip().endswith(CJK + str(i))}
        assert len(cols) == 1


@pytest.mark.parametrize("pointer", POINTERS)
def test_pointer_is_amber_with_colour_and_plain_without(pointer):
    plain = "\n".join(_compose(80, None, color=False, pointer=pointer))
    assert "\x1b" not in plain
    row = next(ln for ln in _compose(80, None, color=True, pointer=pointer, selected=2) if pointer in ln)
    assert row.startswith(f"\x1b[{present.AMBER}m{pointer}\x1b[0m")


class _Tty:
    def __init__(self, tty=True, encoding="utf-8"):
        self._tty, self.encoding = tty, encoding

    def isatty(self):
        return self._tty


APPLE = {"TERM_PROGRAM": "Apple_Terminal", "LANG": "en_US.UTF-8", "TERM": "xterm-256color"}


@pytest.mark.parametrize("env,stream,expected", [
    (APPLE, _Tty(), present.POINT_EMOJI),
    ({**APPLE, "LANG": "en_US.utf8"}, _Tty(), present.POINT_EMOJI),
    ({"WT_SESSION": "x", "LC_ALL": "C.UTF-8"}, _Tty(), present.POINT_EMOJI),
    ({"KITTY_WINDOW_ID": "1", "LANG": "en_US.UTF-8"}, _Tty(), present.POINT_EMOJI),
    ({"TERM": "alacritty", "LANG": "en_US.UTF-8"}, _Tty(), present.POINT_EMOJI),
    ({"TERM_PROGRAM": "ghostty"}, _Tty(encoding="UTF-8"), present.POINT_EMOJI),  # no locale: stream decides
    ({**APPLE, "TERM": "linux"}, _Tty(), present.POINT_ASCII),
    ({**APPLE, "TERM": "dumb"}, _Tty(), present.POINT_ASCII),
    ({**APPLE, "TERM": "vt100"}, _Tty(), present.POINT_ASCII),
    ({**APPLE, "TERM": "screen.xterm-256color", "STY": "1.tty.host"}, _Tty(), present.POINT_ASCII),  # GNU screen
    ({**APPLE, "STY": "1.tty.host"}, _Tty(), present.POINT_ASCII),  # screen with an overridden TERM
    ({**APPLE, "TERM": "screen-256color", "TMUX": "x"}, _Tty(), present.POINT_ASCII),  # old tmux: TERM_PROGRAM leaks
    ({**APPLE, "LANG": "C"}, _Tty(), present.POINT_ASCII),  # the locale wins over a UTF-8 stream
    ({**APPLE, "LC_ALL": "C"}, _Tty(), present.POINT_ASCII),  # LC_ALL outranks LANG
    ({"TERM_PROGRAM": "SomethingElse", "LANG": "en_US.UTF-8"}, _Tty(), present.POINT_ASCII),
    ({"LANG": "en_US.UTF-8"}, _Tty(), present.POINT_ASCII),
    (APPLE, _Tty(tty=False), present.POINT_ASCII),
    (APPLE, object(), present.POINT_ASCII),
    ({**APPLE, "MONITASK_CENTER_POINTER": "ascii"}, _Tty(), present.POINT_ASCII),
    ({"MONITASK_CENTER_POINTER": "EMOJI", "TERM": "linux"}, _Tty(tty=False), present.POINT_EMOJI),
    ({**APPLE, "MONITASK_CENTER_POINTER": "bogus"}, _Tty(), present.POINT_EMOJI),  # invalid: ignored
])
def test_pointer_detection_matrix(env, stream, expected):
    assert present.pointer(env, stream) == expected


def test_no_half_block_bar_is_left_in_the_tui_code():
    import pathlib

    root = pathlib.Path(present.__file__).parent
    assert not [f.name for f in root.glob("*.py") if "\u258c" in f.read_text(encoding="utf-8")]


def test_status_lines_are_grey_italic_so_they_read_as_read_only():
    out = present.compose("t", [("row", "value", "value")], 0, columns=80, lines=None,
                          style=present.Style(True), hint="h", extra=["s0", "s1", "s2"], extra_title="st")
    for text in ("s0", "s1", "s2"):
        assert any(f"\x1b[3;{present.GREY}m{text}" in line for line in out)
    assert not any(f"\x1b[3;{present.GREY}mvalue" in line for line in out)


def test_tagline_is_centred_like_the_logo():
    from monitask_center.tui import logo

    out = _with_logo(30)
    tag = next(line for line in out if "tagline" in line)
    lead = len(tag) - len(tag.lstrip(" "))
    assert abs((99 - present.width(tag)) - lead) <= 1  # panel is 99 wide at 100 columns
    assert lead == (99 - len("tagline")) // 2 and logo.indent(100, logo.BIG) == (99 - 67) // 2
