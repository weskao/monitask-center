"""The --config header logo: the wordmark ``monitask-center`` in two size tiers.

Rows are embedded literals (no runtime FIGlet). :func:`lines` picks the widest tier
that fits the terminal (98 columns wide, else 67, else 43, else none), centres it over
the panel (``columns - 1`` wide, never less than :data:`INDENT` from the edge) and paints
it with a horizontal amber -> teal gradient over every glyph. With colour off the rows come
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

# font thick, 98 columns. Solid strokes, so the wordmark reads as filled rather than
# outlined. Needs INDENT + 98 + SPARE = 101 columns, so it only appears on wide terminals;
# every row is padded to the full width because the tests compare len() per row.
THICK = (
    r"__________              ______                          _____                                     ",
    r"_______ ___________________(_)_  /______ __________  /__      ____________________  /_____________",
    r"__  __ `__ \  __ \_  __ \_  /_  __/  __ `/_  ___/_  //_/_______  ___/  _ \_  __ \  __/  _ \_  ___/",
    r"_  / / / / / /_/ /  / / /  / / /_ / /_/ /_(__  )_  ,<  _/_____/ /__ /  __/  / / / /_ /  __/  /    ",
    r"/_/ /_/ /_/\____//_/ /_//_/  \__/ \__,_/ /____/ /_/|_|        \___/ \___//_/ /_/\__/ \___//_/     ",
)
# font smlant, 67 columns (pagga's M read as an H)
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
TIERS = (THICK, BIG, SMALL)  # widest first
# Rows a fixed-height frame needs before a tier may be drawn; a tier absent here has no
# floor. The main menu at 24 rows fits SMALL plus the first two status lines; BIG is one
# row taller, so below its floor it would push "last check" off screen, and THICK one
# taller again.
# ponytail: tied to the main menu height, recompute if ROWS changes
MIN_ROWS = {THICK: 26, BIG: 25}

# 256-colour steps from present.AMBER (214) to present.TEAL (37).
STOPS = (214, 178, 142, 108, 73, 37)


def tier(columns: int, lines: int | None = None) -> tuple[str, ...]:
    """The widest tier that fits *columns* (indent and one spare column included), or ().

    With *lines* given (a fixed-height TUI frame) a tier also needs its :data:`MIN_ROWS`
    floor.
    """
    for rows in TIERS:
        floor = MIN_ROWS.get(rows)
        if lines is not None and floor is not None and lines < floor:
            continue
        if columns >= INDENT + len(rows[0]) + SPARE:
            return rows
    return ()


def _stop(i: int, span: int) -> str:
    return f"\x1b[38;5;{STOPS[round(i / span * (len(STOPS) - 1))]}m"


def indent(columns: int, rows: tuple[str, ...]) -> int:
    """Left margin that centres *rows* over the ``columns - 1`` wide panel.

    :func:`tier` guarantees ``INDENT + width <= columns - 1``, so the floor never overflows.
    """
    return max(INDENT, (columns - 1 - len(rows[0])) // 2)


def paint(rows: tuple[str, ...], margin: int = INDENT) -> list[str]:
    """*rows* indented by *margin*, every glyph painted with the gradient by column."""
    span = max(len(rows[0]) - 1, 1)
    out = []
    for row in rows:
        cells, last = [" " * margin], ""
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
    margin = indent(columns, rows)
    return paint(rows, margin) if color else [" " * margin + row for row in rows]
