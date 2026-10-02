"""The --config header logo: the wordmark ``monitask-center`` in two size tiers.

Rows are embedded literals (no runtime FIGlet). :func:`lines` picks the widest tier
that fits the terminal (67 columns wide, else 43, else none) and paints it with a
horizontal amber -> teal gradient over every glyph. With colour off the rows come
back as plain glyphs, no escape bytes.

Known limitation: the box-drawing glyphs of the small tier are East-Asian *ambiguous*
width. A terminal set to a CJK locale may draw them 2 cells wide, which would
overflow the width budget; there is no portable way to detect that, so it is not
handled.
"""

from __future__ import annotations

from . import present

INDENT = 2
SPARE = 1  # the last column is never written, so a line can never wrap

# font smslant, 67 columns (pagga's M read as an H)
BIG = (
    r"                   _ __           __                    __         ",
    r"  __ _  ___  ___  (_) /____ ____ / /_____________ ___  / /____ ____",
    r" /  ' \/ _ \/ _ \/ / __/ _ `(_-</  '_/___/ __/ -_) _ \/ __/ -_) __/",
    r"/_/_/_/\___/_//_/_/\__/\_,_/___/_/\_\    \__/\__/_//_/\__/\__/_/   ",
)
# font future, 43 columns
SMALL = (
    "┏┳┓┏━┓┏┓╻╻╺┳╸┏━┓┏━┓╻┏    ┏━╸┏━╸┏┓╻╺┳╸┏━╸┏━┓",
    "┃┃┃┃ ┃┃┗┫┃ ┃ ┣━┫┗━┓┣┻┓╺━╸┃  ┣╸ ┃┗┫ ┃ ┣╸ ┣┳┛",
    "╹ ╹┗━┛╹ ╹╹ ╹ ╹ ╹┗━┛╹ ╹   ┗━╸┗━╸╹ ╹ ╹ ┗━╸╹┗╸",
)
TIERS = (BIG, SMALL)  # widest first
# The main menu at 24 rows fits SMALL plus the first two status lines; BIG is one row
# taller, so below this it would push "last check" off screen.
BIG_MIN_ROWS = 25  # ponytail: tied to the main menu height, recompute if ROWS changes

# 256-colour steps from present.AMBER (214) to present.TEAL (37).
STOPS = (214, 178, 142, 108, 73, 37)


def tier(columns: int, lines: int | None = None) -> tuple[str, ...]:
    """The widest tier that fits *columns* (indent and one spare column included), or ().

    With *lines* given (a fixed-height TUI frame) BIG also needs :data:`BIG_MIN_ROWS`.
    """
    for rows in TIERS:
        if rows is BIG and lines is not None and lines < BIG_MIN_ROWS:
            continue
        if columns >= INDENT + len(rows[0]) + SPARE:
            return rows
    return ()


def _stop(i: int, span: int) -> str:
    return f"\x1b[38;5;{STOPS[round(i / span * (len(STOPS) - 1))]}m"


def paint(rows: tuple[str, ...]) -> list[str]:
    """*rows* indented, every glyph painted with the gradient by column."""
    span = max(len(rows[0]) - 1, 1)
    out = []
    for row in rows:
        cells, last = [" " * INDENT], ""
        for i, ch in enumerate(row):
            code = "" if ch == " " else _stop(i, span)
            if code and code != last:
                cells.append(code)
                last = code
            cells.append(ch)
        out.append("".join(cells) + "\x1b[0m")
    return out


def lines(columns: int, color: bool, height: int | None = None) -> list[str]:
    """The logo for a terminal *columns* wide (and *height* rows, if fixed); [] when none fits."""
    rows = tier(columns, height)
    if not rows:
        return []
    return paint(rows) if color else [" " * INDENT + row for row in rows]
