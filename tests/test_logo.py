import os
import re

import pytest

from monitask_center import i18n
from monitask_center.tui import logo, present

SGR = re.compile(r"\x1b\[38;5;(\d+)m")


@pytest.mark.parametrize(("rows", "wide", "high"), [(logo.BIG, 67, 4), (logo.SMALL, 43, 3)])
def test_every_tier_row_has_the_declared_width(rows, wide, high):
    assert len(rows) == high
    assert {len(row) for row in rows} == {wide}
    assert {present.width(row) for row in rows} == {wide}


def test_colour_off_returns_the_rows_unchanged_and_without_escapes():
    for columns, rows in ((100, logo.BIG), (50, logo.SMALL)):
        out = logo.lines(columns, False)
        assert out == ["  " + row for row in rows]
        assert not any("\x1b" in line for line in out)


@pytest.mark.parametrize(("columns", "rows"), [
    (45, ()), (46, logo.SMALL), (69, logo.SMALL), (70, logo.BIG), (200, logo.BIG),
])
def test_tier_switches_at_the_exact_boundaries(columns, rows):
    assert logo.tier(columns) == rows
    assert (logo.lines(columns, False) != []) == bool(rows)


def test_logo_never_reaches_the_last_column():
    for columns in range(1, 200):
        for color in (False, True):
            assert all(present.width(line) <= columns - 1 for line in logo.lines(columns, color))


def test_gradient_runs_from_amber_to_teal():
    assert f"38;5;{logo.STOPS[0]}" == present.AMBER and f"38;5;{logo.STOPS[-1]}" == present.TEAL
    for columns in (100, 50):
        painted = logo.lines(columns, True)
        used = [int(n) for line in painted for n in SGR.findall(line) if int(n) in logo.STOPS]
        assert {logo.STOPS[0], logo.STOPS[-1]} <= set(used)
        assert all(line.endswith("\x1b[0m") for line in painted)
    small = logo.lines(50, True)[0]
    assert small.startswith("  \x1b[38;5;214m┏")  # first glyph: first stop
    assert SGR.findall(small)[-1] == str(logo.STOPS[-1])  # last glyph: last stop


@pytest.mark.parametrize(("columns", "lines", "rows"), [
    (100, None, logo.BIG), (100, 25, logo.BIG), (100, 24, logo.SMALL), (50, 40, logo.SMALL), (45, 40, ()),
])
def test_big_tier_also_needs_enough_rows_when_the_height_is_fixed(columns, lines, rows):
    assert logo.tier(columns, lines) == rows


def test_tagline_key_exists_is_short_and_is_what_the_menu_draws(monkeypatch):
    from monitask_center.tui import menu

    text = i18n.t("tui.tagline")
    assert text != "tui.tagline" and len(text) <= 14 and present.width(text) <= 28
    monkeypatch.setattr(menu, "_terminal_size", lambda _out: os.terminal_size((100, 30)))
    monkeypatch.setenv("NO_COLOR", "1")

    class Tty:
        def isatty(self):
            return True

    state = menu.State(menu.config.load(), lambda: ["a", "b", "c"])
    assert any(text in line for line in menu._main_lines(state, 0, Tty()))
