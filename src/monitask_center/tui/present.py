"""Terminal presentation for the --config menu: width math, fitting, palette, frame.

Look: an open frame (heavy amber rules above and below, no side borders) with a
amber pointer (:func:`pointer`) on the cursor row and teal values. Every line is fitted to
``columns - 1`` so nothing ever wraps, which is what keeps the cursor-up redraw
exact. Colour only when the stream is a TTY and ``NO_COLOR`` is unset; with
colour off :class:`Style` emits no escape bytes at all.
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

ELLIPSIS = "…"
RULE = "━"
THIN = "┄"
POINT_EMOJI = "\U0001F449 "  # pointing hand (East-Asian Wide, 2 columns) + a space, 3 columns
POINT_ASCII = ">  "  # padded to the same 3 columns, so labels never shift between styles
EMOJI_TERM_PROGRAMS = frozenset({"Apple_Terminal", "iTerm.app", "vscode", "WezTerm", "ghostty", "Hyper", "Tabby"})
EMOJI_TERMS = ("xterm-kitty", "alacritty", "foot")
NO_EMOJI_TERMS = frozenset({"linux", "dumb", "vt100"})
MARK = "◆"

AMBER = "38;5;214"
TEAL = "38;5;37"
GREY = "38;5;245"
RED = "38;5;203"
GREEN = "38;5;114"
BOLD = "1"
DIM = "2"
ITALIC = "3"

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def width(text: str) -> int:
    """Display columns: ANSI stripped, East-Asian Wide/Fullwidth = 2, combining = 0."""
    return sum(
        0 if unicodedata.combining(ch) else 2 if unicodedata.east_asian_width(ch) in "WF" else 1
        for ch in strip_ansi(text)
    )


def fit(text: str, columns: int) -> str:
    """*text* clipped to *columns* display columns, ending in an ellipsis when clipped."""
    if columns <= 0:
        return ""
    if width(text) <= columns:
        return text
    kept, used = [], 0
    for ch in text:
        used += width(ch)
        if used > columns - 1:
            break
        kept.append(ch)
    return "".join(kept) + ELLIPSIS


def pad(text: str, columns: int) -> str:
    text = fit(text, columns)
    return text + " " * (columns - width(text))


def frame_rows(lines: Sequence[str], columns: int) -> int:
    """Physical terminal rows *lines* occupy once wrapped at *columns*."""
    columns = max(columns, 1)
    return sum(max(1, -(-width(line) // columns)) for line in lines)


def is_tty(stream) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def color_enabled(stream) -> bool:
    return is_tty(stream) and not os.environ.get("NO_COLOR")


def pointer(env=None, stream=None) -> str:
    """The cursor-row marker, 2 display columns wide either way. Decide once per session.

    ``MONITASK_CENTER_POINTER=emoji|ascii`` wins. Otherwise the emoji only on a UTF-8 TTY whose
    terminal is known to draw it (never the Linux console, a dumb terminal or GNU screen); else ``> ``.
    """
    env = os.environ if env is None else env
    forced = env.get("MONITASK_CENTER_POINTER", "").strip().lower()
    if forced in ("emoji", "ascii"):
        return POINT_EMOJI if forced == "emoji" else POINT_ASCII
    term = env.get("TERM", "")
    locale = next((env[k] for k in ("LC_ALL", "LC_CTYPE", "LANG") if env.get(k)), "")
    encoding = locale or str(getattr(stream, "encoding", "") or "")  # no locale set: trust the stream
    utf8 = "utf8" in encoding.lower().replace("-", "")
    known = (env.get("TERM_PROGRAM") in EMOJI_TERM_PROGRAMS or bool(env.get("WT_SESSION"))
             or bool(env.get("KITTY_WINDOW_ID")) or term.startswith(EMOJI_TERMS))
    # GNU screen forwards the outer TERM_PROGRAM but draws U+1F449 as a 1-column U+FFFD.
    screen = term.startswith("screen") or bool(env.get("STY"))
    if is_tty(stream) and utf8 and term not in NO_EMOJI_TERMS and not screen and known:
        return POINT_EMOJI
    return POINT_ASCII


@dataclass(frozen=True)
class Style:
    color: bool
    pointer: str = POINT_ASCII

    def paint(self, text: str, *codes: str) -> str:
        if not (self.color and codes and text):
            return text
        return f"\x1b[{';'.join(codes)}m{text}\x1b[0m"


INFO = (ITALIC, GREY)  # read-only status lines: grey italic, unlike the teal editable values
TONES = {"value": (TEAL,), "muted": (GREY,), "ok": (GREEN,), "error": (RED,), "info": (AMBER,),
         "input": (BOLD,), "": ()}


def compose(
    title: str,
    items: Sequence[tuple[str, str, str]],
    selected: int | None,
    *,
    columns: int,
    lines: int | None,
    style: Style,
    hint: str,
    hint2: str = "",
    message: tuple[str, str] = ("", ""),
    help_text: str = "",
    extra: Sequence[str] = (),
    extra_title: str = "",
    logo: Sequence[str] = (),
    tagline: str = "",
) -> list[str]:
    """The panel as a list of lines, each at most ``columns - 1`` wide.

    *items* are ``(label, value, tone)``. With *lines* given the panel is shorter
    than the terminal: notes go first (status lines, then the rule, the second
    hint, the help line), then the row list becomes a viewport around *selected*.
    The message and hint lines are always reserved, so the height never changes
    while the cursor moves.

    *logo* (ready-made lines, drawn above the title rule) is an atomic optional block:
    shown whole or not at all, and *tagline* only together with it. Notes keep this
    priority, highest first: help, second hint, status heading + first status line
    (the Monitask state), logo, rule, second status line, tagline, remaining status
    lines. A logo or tagline that does not fit is skipped; the first other block that
    does not fit ends the list.
    """
    w = max(columns - 1, 12)
    label_w = min(max((width(lbl) for lbl, _, _ in items), default=0), max(w // 2 - 3, 4))
    value_w = max(w - 5 - label_w, 1)

    def row(i: int) -> str:
        label, value, tone = items[i]
        cell = pad(label, label_w)
        value = fit(value, value_w)
        if i == selected:
            return (f"{style.paint(style.pointer, AMBER)}{style.paint(cell, BOLD)}  "
                    f"{style.paint(value, BOLD, *TONES[tone])}")
        return f"   {cell}  {style.paint(value, *TONES[tone])}"

    def note(text: str, *codes: str) -> str:
        # Newlines/control bytes (exception text, status probes) would break the row count.
        clean = "".join(ch if ch.isprintable() else " " for ch in strip_ansi(text))
        return "  " + style.paint(fit(clean, w - 2), *codes)

    # (group, lines) in keep-priority order; a block that does not fit is skipped whole.
    atoms = [("help", [note(help_text, DIM)])] if help_text else []
    atoms += [("help", [note(hint2, DIM)])] if hint2 else []
    if extra:
        head = f"{THIN} {fit(extra_title, w - 8)} "
        atoms.append(("status", [note(head + THIN * (w - 2 - width(head)), AMBER), note(extra[0], *INFO)]))
    if logo:
        atoms.append(("logo", list(logo)))
    atoms.append(("sep", [note(THIN * (w - 2), AMBER)]))
    atoms += [("status", [note(x, *INFO)]) for x in extra[1:2]]
    if logo and tagline:
        text = fit(tagline, w - 2)  # centred under the logo, which is centred over the panel
        atoms.append(("tagline", [" " * max((w - width(text)) // 2, 2) + style.paint(text, DIM)]))
    atoms += [("status", [note(x, *INFO)]) for x in extra[2:]]
    count, start, room = len(items), 0, None
    if lines is not None:
        room = max(lines - 1 - 4, 1)  # top rule, message, hint, bottom rule are fixed
        count = min(len(items), room)
        room -= count
        if selected is not None and count < len(items):
            start = min(max(selected - count // 2, 0), len(items) - count)
    kept: dict[str, list[str]] = {}
    for group, block in atoms:
        if group == "tagline" and "logo" not in kept:
            continue
        if room is not None and len(block) > room:
            if group in ("logo", "tagline"):
                continue
            break
        kept.setdefault(group, []).extend(block)
        room = None if room is None else room - len(block)
    shown_help, shown_sep = kept.get("help", []), kept.get("sep", [])
    shown_status = kept.get("status", [])

    if count < len(items) and selected is not None:
        title = f"{title} {selected + 1}/{len(items)}"
    title = fit(title, w - 7)
    top = (style.paint(f"{RULE}{RULE} {MARK} ", AMBER) + style.paint(title, BOLD, AMBER)
           + style.paint(" " + RULE * (w - 6 - width(title)), AMBER))
    kind, text = message
    out = [*kept.get("logo", []), *kept.get("tagline", []), top, *(row(i) for i in range(start, start + count)), *shown_status, *shown_sep,
           *shown_help, note(text, *TONES.get(kind, ())) if text else "",
           note(hint, DIM), style.paint(RULE * w, AMBER)]
    return out
