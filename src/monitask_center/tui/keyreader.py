"""Key input for the --config menu: POSIX cbreak single keys, line reads for non-TTYs.

The menu only enters the key path when BOTH streams are a TTY (:func:`is_interactive`);
everything else uses :func:`read_line`, which treats EOF as "quit" and never blocks
on a dead pipe. Keys come back as semantic names so the menu never sees a byte.
"""

from __future__ import annotations

import contextlib
import os
import select
from collections.abc import Iterator
from typing import IO

_SEQUENCES = {
    "\x1b[A": "up", "\x1bOA": "up", "\x1b[B": "down", "\x1bOB": "down",
    "\x1b[C": "right", "\x1bOC": "right", "\x1b[D": "left", "\x1bOD": "left",
    "\x1b[3~": "delete", "\x1b": "esc", "\r": "enter", "\n": "enter", " ": "space",
    "\x7f": "delete", "\x08": "delete", "": "quit", "\x04": "quit",
    "q": "quit", "Q": "quit", "k": "up", "j": "down", "h": "left", "l": "right",
    "r": "reset", "R": "reset_all", "y": "yes", "Y": "yes", "a": "add", "d": "delete", "x": "delete",
}
_ESC_WAIT = 0.12  # a bare Esc has nothing behind it; an arrow's tail arrives in one burst


def is_interactive(stdin: IO[str], stdout: IO[str]) -> bool:
    """True only when both streams are a terminal: reading keys needs one, redrawing the other."""
    try:
        return bool(stdin.isatty() and stdout.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def read_line(stdin: IO[str]) -> str | None:
    """One stripped line, or None at EOF."""
    try:
        line = stdin.readline()
    except (OSError, ValueError):
        return None
    return None if line == "" else line.strip()


@contextlib.contextmanager
def key_session(stdin: IO[str]) -> Iterator[None]:
    """Hold cbreak (no echo, no line buffering) for the whole session.

    Per-key raw mode would leave the terminal echoing between reads, so a key
    pressed during a redraw lands on screen and shifts the frame. cbreak keeps
    output processing (no staircase) and Ctrl+C as a signal. Restored on any exit.
    """
    try:
        import termios
        import tty

        fd = stdin.fileno()
        saved = termios.tcgetattr(fd)
    except Exception:  # noqa: BLE001 - no termios / not a real tty (termios.error is not OSError)
        saved = None
    if saved is None:
        yield
        return
    try:
        tty.setcbreak(fd, termios.TCSANOW)  # TCSANOW: keep typeahead
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def _read_raw(fd: int) -> str:
    first = os.read(fd, 1)
    if not first:
        return ""
    if first == b"\x1b":
        if not select.select([fd], [], [], _ESC_WAIT)[0]:
            return "\x1b"
        seq = first + os.read(fd, 1)
        if seq[-1:] not in (b"[", b"O"):
            return "\x1b" + seq[-1:].decode("utf-8", "replace")  # Alt+key: ignored by callers
        while not 0x40 <= seq[-1] <= 0x7E or len(seq) == 2:
            more = os.read(fd, 1)
            if not more:
                break
            seq += more
        return seq.decode("utf-8", "replace")
    lead = first[0]
    extra = 3 if lead >= 0xF0 else 2 if lead >= 0xE0 else 1 if lead >= 0xC0 else 0
    data = first + (os.read(fd, extra) if extra else b"")
    return data.decode("utf-8", "replace")


def read_key(stdin: IO[str]) -> str:
    """One semantic key: up/down/left/right/enter/space/esc/quit/reset/reset_all/
    yes/add/delete, or "other"."""
    try:
        return _SEQUENCES.get(_read_raw(stdin.fileno()), "other")
    except OSError:  # terminal hung up (EIO): leave cleanly
        return "quit"


def read_edit_key(stdin: IO[str]) -> str:
    """One keystroke for a text field: "enter", "backspace", "escape", "" (ignored
    key), or the printable character typed."""
    try:
        raw = _read_raw(stdin.fileno())
    except OSError:
        return "escape"
    if raw in ("\r", "\n"):
        return "enter"
    if raw in ("\x7f", "\x08"):
        return "backspace"
    if raw in ("\x1b", "", "\x04"):
        return "escape"
    return raw if raw.isprintable() and not raw.startswith("\x1b") else ""
