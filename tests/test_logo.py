import os
import re

import pytest

from monitask_center import i18n
from monitask_center.tui import logo, present

SGR = re.compile(r"\x1b\[38;5;(\d+)m")
BOLD_SGR = re.compile(r"\x1b\[1;38;5;(\d+)m")  # logo._stop() leads with bold


@pytest.mark.parametrize(("rows", "wide", "high"), [(logo.THICK, 98, 5), (logo.BIG, 89, 4), (logo.SMALL, 43, 3)])
def test_every_tier_row_has_the_declared_width(rows, wide, high):
    assert len(rows) == high
    assert {len(row) for row in rows} == {wide}
    assert {present.width(row) for row in rows} == {wide}


def test_colour_off_returns_the_rows_unchanged_and_without_escapes():
    for columns, rows in ((120, logo.THICK), (100, logo.BIG), (50, logo.SMALL)):
        out = logo.lines(columns, False)
        assert out == [" " * logo.indent(columns, rows) + row for row in rows]
        assert not any("\x1b" in line for line in out)


@pytest.mark.parametrize(("columns", "rows"), [
    (45, ()), (46, logo.SMALL), (91, logo.SMALL), (92, logo.BIG),
    (100, logo.BIG), (101, logo.THICK), (200, logo.THICK),
])
def test_tier_switches_at_the_exact_boundaries(columns, rows):
    assert logo.tier(columns) == rows
    assert (logo.lines(columns, False) != []) == bool(rows)


def test_logo_never_reaches_the_last_column():
    for columns in range(1, 200):
        for color in (False, True):
            assert all(present.width(line) <= columns - 1 for line in logo.lines(columns, color))


def test_gradient_sweeps_the_wheel_bold_and_stays_inside_the_panel_palette():
    assert f"38;5;{logo.STOPS[0]}" == present.AMBER and f"38;5;{logo.STOPS[-1]}" == present.TEAL
    for columns in (100, 50):
        painted = logo.lines(columns, True)
        used = {int(n) for line in painted for n in BOLD_SGR.findall(line)}
        assert {logo.STOPS[0], logo.STOPS[-1]} <= used <= set(logo.STOPS)
        assert all(line.endswith("\x1b[0m") for line in painted)
        assert all("\x1b[1;38;5;" in line for line in painted)  # every glyph bold
    small = logo.lines(50, True)[0]
    assert small.lstrip(" ").startswith("\x1b[1;38;5;214m┏")  # first glyph: first stop
    assert BOLD_SGR.findall(small)[-1] == str(logo.STOPS[-1])  # last glyph: last stop


def test_sweep_visits_several_distinct_hues_not_just_the_two_ends():
    """Guards a regression to the old two-tone wash: a 12-step sweep must use many stops."""
    assert len(set(logo.STOPS)) >= 10
    painted = logo.lines(100, True)
    assert len({int(n) for line in painted for n in BOLD_SGR.findall(line)}) >= 8


def test_the_m_reads_as_an_m_and_not_an_h():
    """The previous slanted face's M was flagged in-code as reading as an H.

    The solid face must keep a wide M whose centre wedge spans the full interior and
    touches both stems; an H would instead be two stems joined by a mid bar.
    """
    rows = logo.BIG
    # The M is the leading glyph: from the first column up to the first gap. Deriving it
    # from the art means a future re-draw cannot quietly shrink the M past the assert below.
    m_width = next(i for i, ch in enumerate(rows[0]) if ch == " ")
    assert m_width >= 7, f"M is only {m_width} cells wide"
    top, upper, lower = rows[0][:m_width], rows[1][:m_width], rows[2][:m_width]
    # centre wedge present on both upper rows and spanning the interior
    assert top[0] == "█" and top[-1] == "█" and set(top[1:-1]) == {"▄"}
    assert upper[0] == "█" and upper[-1] == "█" and set(upper[1:-1]) == {"▀"}
    # row 2 is the two bare stems -- an H would put a bar across the middle here
    assert lower[0] == "█" and lower[-1] == "█" and set(lower[1:-1]) == {" "}


@pytest.mark.parametrize(("columns", "lines", "rows"), [
    (120, None, logo.THICK), (120, 26, logo.THICK), (120, 25, logo.BIG),
    (100, None, logo.BIG), (100, 25, logo.BIG), (100, 24, logo.SMALL),
    (50, 40, logo.SMALL), (45, 40, ()),
])
def test_a_tier_also_needs_enough_rows_when_the_height_is_fixed(columns, lines, rows):
    assert logo.tier(columns, lines) == rows


def test_tagline_key_exists_is_short_and_is_what_the_menu_draws(monkeypatch):
    from monitask_center.tui import menu

    text = i18n.t("tui.tagline")
    assert text != "tui.tagline" and len(text) <= 14 and present.width(text) <= 28
    monkeypatch.setattr(menu, "_terminal_size", lambda _out: os.terminal_size((100, 35)))
    monkeypatch.setenv("NO_COLOR", "1")

    class Tty:
        def isatty(self):
            return True

    state = menu.State(menu.config.load(), lambda: ["a", "b", "c"])
    assert any(text in line for line in menu._main_lines(state, 0, Tty()))


@pytest.mark.parametrize("columns", [46, 69, 70, 80, 101, 120, 200])
def test_logo_is_centred_over_the_panel_and_never_wraps(columns):
    rows = logo.tier(columns)
    panel = columns - 1
    for color in (False, True):
        for line, row in zip(logo.lines(columns, color), rows):
            left = present.width(line) - len(row)
            assert left >= logo.INDENT and present.width(line) <= panel
            assert left == logo.indent(columns, rows)
    left = logo.indent(columns, rows)
    if left > logo.INDENT:  # not pinned to the floor: margins differ by at most one column
        assert abs((panel - len(rows[0]) - left) - left) <= 1
