"""The interactive --config menu.

Rows are data (:data:`ROWS`); one generic loop renders and edits them. Two surfaces:
the arrow-key TUI (both streams a TTY; redraws in place by walking the cursor up
the frame it drew and erasing with ``ESC[J``) and a numbered line-based fallback
(pipes, CI, tests) that never emits an escape byte. Every change is validated,
saved at once (save-as-you-go) and, only when the launchd schedule depends on it
(start/end/interval/overtime time), followed by :func:`launchd.sync` - which never installs.
"""

from __future__ import annotations

import os
import select
import signal
import subprocess
import sys
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import IO

from .. import __version__, config, i18n, launchd, telegram, update_check
from ..i18n import t
from . import keyreader, logo, present

DEFAULTS = config.Settings()
SYNC_FIELDS = ("start_hour", "end_hour", "interval_hours", "overtime_minutes")
EDIT_MAX = 40  # typed date/range field: a paste-flood backstop, not a format check
CARET = "▏"


@dataclass(frozen=True)
class Row:
    key: str          # tui.row.<key> label, tui.help.<key> help
    kind: str         # hour | clock | interval | toggle | language | vacation | install
    field: str = ""
    low: int = 0
    high: int = 0


ROWS = (
    Row("start", "hour", "start_hour", 0, 22),
    Row("end", "hour", "end_hour", 1, 23),
    Row("interval", "interval", "interval_hours"),
    Row("auto_play_pause", "toggle", "auto_play_pause"),
    Row("lunch_skip", "toggle", "lunch_skip"),
    Row("lunch_start", "hour", "lunch_start_hour", 0, 23),
    Row("lunch_end", "hour", "lunch_end_hour", 1, 24),
    Row("overtime_prompt", "toggle", "overtime_prompt"),
    Row("overtime_time", "clock", "overtime_minutes", config.OVERTIME_EARLIEST, config.OVERTIME_LATEST),
    Row("language", "language", "language"),
    Row("update_check", "toggle", "update_check"),
    Row("telegram", "toggle", "telegram"),
    # The token is the only secret: it is NOT a Settings field, so `field` is empty and the
    # value lives in the OS credential store. `text` is the ordinary chat id in config.json.
    Row("telegram_token", "secret"),
    Row("telegram_chat", "text", "telegram_chat_id"),
    Row("vacation", "vacation", "vacation"),
    Row("install", "install"),
)
ADJUSTABLE = ("hour", "clock", "interval", "toggle", "language")
# Rows edited by typing rather than by stepping a value.
TYPED = ("text", "secret")
TEXT_MAX = 128  # a pasted token/chat id, not a format check (telegram_kit's own bound)
VACATION = next(row for row in ROWS if row.kind == "vacation")


@dataclass
class State:
    settings: config.Settings
    status_source: Callable[[], list[str]] | None = None
    note: tuple[str, str] = ("", "")
    failed: bool = False
    draft: str = ""  # vacation text kept after a rejected entry
    # The TUI defers launchd sync and the status re-probe until the cursor leaves the
    # row: holding an arrow must not bootout/bootstrap launchd or spawn a probe per step.
    defer: bool = False
    pointer: str = present.POINT_ASCII  # chosen once per interactive session in config_menu
    pending_sync: bool = field(default=False, init=False)
    stale: bool = field(default=False, init=False)
    _install: tuple[bool, bool] | None = field(default=None, init=False, repr=False)
    _status: list[str] | None = field(default=None, init=False, repr=False)

    def install(self) -> tuple[bool, bool]:
        """(installed, loaded), probed once per change: launchctl is a subprocess."""
        if self._install is None:
            installed = launchd.is_installed()
            try:
                loaded = installed and bool(launchd.loaded())
            except OSError:
                loaded = False
            self._install = (installed, loaded)
        return self._install

    def status(self) -> list[str]:
        if self.status_source is None:
            return []
        if self._status is None:
            try:
                self._status = [str(x) for x in (self.status_source() or [])]
            except Exception as exc:  # noqa: BLE001 - a status probe must never kill the menu
                self._status = [t("tui.status.error", error=exc)]
        return self._status

    def refresh(self) -> None:
        self._install = self._status = None


# ── values and edits ─────────────────────────────────────────────────────────


def _bounds(row: Row, s: config.Settings) -> tuple[int, int]:
    if row.kind == "interval":
        return 1, config.max_interval(s.start_hour, s.end_hour)
    return row.low, row.high


def _value(state: State, row: Row, s: config.Settings | None = None) -> str:
    s = s or state.settings
    v = getattr(s, row.field) if row.field else None
    if row.kind == "hour":
        return f"{v:02d}:00"
    if row.kind == "clock":
        return config.clock(v)
    if row.kind == "interval":
        return t("tui.value.hours", n=v)
    if row.kind == "toggle":
        return t("common.on" if v else "common.off")
    if row.kind == "language":
        return f"{t('tui.language.self', v)} ({v})"
    if row.kind == "vacation":
        return t("tui.value.vacation", count=len(v)) if v else t("tui.value.vacation_none")
    if row.kind == "secret":
        stored = telegram.token()  # never the raw value: masked, or where it comes from
        if stored:
            return telegram.mask(stored)
        return (t("tui.value.from_env") if os.environ.get(telegram.ENV_KEYS[0])
                else t("tui.value.unset"))
    if row.kind == "text":
        return v or os.environ.get(telegram.ENV_KEYS[1]) or t("tui.value.unset")
    installed, loaded = state.install()
    if not installed:
        return t("tui.install.not_installed")
    return t("tui.install.loaded" if loaded else "tui.install.not_loaded")


def _with(s: config.Settings, row: Row, value: object) -> config.Settings:
    new = replace(s, **{row.field: value})
    if row.field in ("start_hour", "end_hour"):  # interval follows the window down
        new = replace(new, interval_hours=min(new.interval_hours,
                                              config.max_interval(new.start_hour, new.end_hour)))
    return new


def _step(s: config.Settings, row: Row, direction: int) -> config.Settings:
    current = getattr(s, row.field)
    if row.kind == "toggle":
        return _with(s, row, not current)
    if row.kind == "language":
        names = i18n.available() or [i18n.DEFAULT_LANGUAGE]
        index = names.index(current) if current in names else -1
        return _with(s, row, names[(index + direction) % len(names)])
    low, high = _bounds(row, s)
    step = config.OVERTIME_STEP if row.kind == "clock" else 1
    return _with(s, row, min(max(current + direction * step, low), high))


def _commit(state: State, new: config.Settings, done: str = "tui.saved") -> bool:
    """Validate, save, re-sync launchd when the schedule changed. Result goes to state.note."""
    old = state.settings
    if new == old:
        state.note = ("info", t("tui.unchanged"))
        return False
    try:
        new = config.validate(new)
    except ValueError as exc:
        state.note = ("error", t(f"tui.error.{exc}"))
        return False
    try:
        config.save(new)
    except OSError as exc:
        state.failed = True
        state.note = ("error", t("tui.error.save", error=exc))
        return False
    state.settings = new
    i18n.set_language(new.language)  # the next frame is drawn in the new language
    if old.language != new.language:
        state.refresh()  # status lines are rendered text: re-probe in the new language now
    state.stale = True
    state.note = ("ok", t(done))
    if any(getattr(old, f) != getattr(new, f) for f in SYNC_FIELDS):
        state.pending_sync = True
    if not state.defer:
        _settle(state)
    return True


def _settle(state: State) -> bool:
    """Run deferred work: launchd sync (never installs), status re-probe. True if synced."""
    synced = state.pending_sync
    if synced:
        state.pending_sync = False
        try:
            result, jobs = launchd.sync(state.settings)
        except (OSError, subprocess.CalledProcessError) as exc:
            state.failed = True
            state.note = ("error", t("tui.error.sync", error=launchd.describe_error(exc)))
        else:
            state.note = ("info" if result == "not_installed" else "ok",
                          launchd.sync_message(result, jobs))
    if state.stale or synced:
        state.stale = False
        state.refresh()
    return synced


def _resettable(row: Row) -> bool:
    return row.kind in ADJUSTABLE or row.kind in TYPED


def _clear_secret(state: State) -> bool:
    """Remove the stored token. An empty value deletes the item rather than storing a blank."""
    if telegram.set_token(""):
        state.note = ("ok", t("tui.reset_done"))
        return True
    state.note = ("error", t("tui.error.no_store", backend=telegram.backend_label()))
    return False


def _reset_row(state: State, row: Row) -> bool:
    if row.kind == "secret":  # not a Settings field: it lives in the credential store
        return _clear_secret(state)
    return _commit(state, _with(state.settings, row, getattr(DEFAULTS, row.field)), "tui.reset_done")


def _reset_all(state: State) -> bool:
    """Every setting back to default; vacation days are data, not a setting, and stay."""
    base = replace(DEFAULTS, vacation=state.settings.vacation)
    return _clear_secret(state) & _commit(state, base, "tui.reset_done")


def _row_question(state: State, row: Row) -> str:
    if row.kind == "secret":  # no default to preview: the value is not in the settings at all
        return t("tui.prompt.confirm_row", label=t(f"tui.row.{row.key}"),
                 value=t("tui.value.unset"))
    preview = _value(state, row, _with(state.settings, row, getattr(DEFAULTS, row.field)))
    return t("tui.prompt.confirm_row", label=t(f"tui.row.{row.key}"), value=preview)


def _apply_typed(state: State, row: Row, editing: str) -> None:
    """Validate and store a typed row. The token goes to the credential store, never to JSON."""
    value = editing.strip()
    if row.kind == "secret":
        if not value:
            state.note = ("info", t("tui.unchanged"))  # blank keeps the stored token
            return
        if not telegram.TOKEN_RE.match(value):
            state.note = ("error", t("tui.error.token_format"))
            return
        if not telegram.set_token(value):
            state.note = ("error", t("tui.error.no_store", backend=telegram.backend_label()))
            return
        state.note = ("ok", t("tui.saved"))
        return
    if not value:
        state.note = ("info", t("tui.unchanged"))
        return
    if not telegram.CHAT_RE.match(value):
        state.note = ("error", t("tui.error.chat_format"))
        return
    _commit(state, _with(state.settings, row, value))


def _typed_tui(state: State, row: Row, stdin: IO[str], screen: _Screen,
               render: Callable[[], list[str]]) -> None:
    """Type a value on the note line; a secret echoes ``*`` and is never drawn in full."""
    secret = row.kind == "secret"
    editing = ""
    hint = t("tui.prompt.secret_keep") if secret else t("tui.prompt.text_edit")
    while True:
        shown = "*" * len(editing) if secret else editing
        state.note = ("input", f"{hint} {shown}{CARET}")
        screen.draw(render())
        if _wait(stdin):
            continue  # resized: redraw at the top of the loop
        key = keyreader.read_edit_key(stdin)
        if key == "escape":
            state.note = ("", "")
            return
        if key == "enter":
            _apply_typed(state, row, editing)
            return
        if key == "backspace":
            editing = editing[:-1]
        elif key and len(editing) < TEXT_MAX:
            editing += key


def _vacation_add(state: State, text: str) -> bool:
    try:
        days = config.parse_dates(text)
    except ValueError as exc:
        state.note = ("error", t(f"tui.error.vacation_{exc}"))
        return False
    merged = tuple(sorted({*state.settings.vacation, *(d.isoformat() for d in days)}))
    added = len(merged) - len(state.settings.vacation)
    if not _commit(state, replace(state.settings, vacation=merged)):
        return state.note[0] != "error"  # nothing new to add is not an error
    state.note = ("ok", t("tui.vacation.added", count=added))
    return True


def _vacation_remove(state: State, text: str) -> bool:
    day = config.parse_date(text)
    if day is None or day.isoformat() not in state.settings.vacation:
        state.note = ("error", t("tui.vacation.missing", day=text.strip()))
        return False
    left = tuple(d for d in state.settings.vacation if d != day.isoformat())
    if _commit(state, replace(state.settings, vacation=left)):
        state.note = ("ok", t("tui.vacation.removed", day=day.isoformat()))
        return True
    return False


# ── rendering ────────────────────────────────────────────────────────────────


def _terminal_size(out: IO[str]) -> os.terminal_size:
    """The terminal *out* draws on; 80x24 when it is not one."""
    try:
        size = os.get_terminal_size(out.fileno())
    except (AttributeError, OSError, ValueError):
        size = os.terminal_size((0, 0))
    return size if size.columns and size.lines else os.terminal_size((80, 24))


def _style(state: State, out: IO[str], numbered: bool) -> present.Style:
    """Colour only on the arrow-key surface: the numbered fallback never emits an escape byte,
    even when stdout happens to be a TTY (stdin is the one that is not)."""
    return present.Style(not numbered and present.color_enabled(out), state.pointer)


def _app_version() -> str:
    try:
        return version("monitask-center")
    except PackageNotFoundError:
        return __version__


def _main_lines(state: State, selected: int | None, out: IO[str]) -> list[str]:
    """The settings panel; *selected* None = the numbered fallback (all rows, numbered)."""
    items = []
    for i, row in enumerate(ROWS):
        label = t(f"tui.row.{row.key}")
        if selected is None:
            label = f"{i + 1:>2}) {label}"
        value = _value(state, row)
        if i == selected and row.kind in ADJUSTABLE:
            value = f"‹ {value} ›"
        items.append((label, value, "muted" if row.kind == "install" else "value"))
    size = _terminal_size(out)
    numbered = selected is None
    style = _style(state, out, numbered)
    header = logo.lines(size.columns, style.color, None if numbered else size.lines)
    return present.compose(
        f"{t('tui.title')} v{_app_version()}", items, selected,
        columns=size.columns, lines=None if numbered else size.lines,
        style=style, logo=header, tagline=t("tui.tagline") if header else "",
        hint=t("tui.hint.numbered", n=len(ROWS)) if numbered else t("tui.hint.keys"),
        hint2="" if numbered else t("tui.hint.keys2"),
        message=state.note,
        help_text="" if numbered else t(f"tui.help.{ROWS[selected].key}"),
        extra=state.status(), extra_title=t("tui.status.heading"),
    )


def _vacation_lines(state: State, selected: int | None, out: IO[str],
                    editing: str | None = None) -> list[str]:
    days = state.settings.vacation
    numbered = selected is None
    add_value = _value(state, VACATION)
    items = [] if numbered else [(t("tui.vacation.add"), add_value, "value")]
    items += [(f"{i:>2}) {d}" if numbered else d, "", "value") for i, d in enumerate(days, 1)]
    if numbered and not days:
        items = [(t("tui.value.vacation_none"), "", "muted")]
    message = state.note
    if editing is not None:
        message = ("input", f"{t('tui.vacation.prompt')}{editing}{CARET}")
    if numbered:
        hint, help_text = t("tui.hint.vacation_numbered"), ""
    else:
        hint = t("tui.hint.vacation_edit" if editing is not None else "tui.hint.vacation")
        help_text = t("tui.help.vacation_add" if selected == 0 else "tui.help.vacation_day")
    size = _terminal_size(out)
    return present.compose(
        t("tui.vacation.title"), items, selected,
        columns=size.columns, lines=None if numbered else size.lines,
        style=_style(state, out, numbered),
        hint=hint, message=message, help_text=help_text,
        extra=[str(config.config_path().resolve())],
        extra_title=t("tui.vacation.file"),
    )


# ── arrow-key surface ────────────────────────────────────────────────────────


class _Screen:
    """Redraw in place: walk up the physical rows last drawn, erase, print anew.

    After a resize that count is stale (the terminal re-wrapped the old frame), so
    the screen is cleared from the top instead of walking up.
    """

    def __init__(self, out: IO[str]) -> None:
        self.out = out
        self.rows = 0  # physical rows of the last frame, counted at the width it was drawn at
        self.size = (0, 0)  # (columns, lines) the last frame was drawn at

    def draw(self, lines: list[str]) -> None:
        size = _terminal_size(self.out)
        if self.rows and (size.columns, size.lines) != self.size:
            self.out.write("\x1b[H\x1b[2J")
        elif self.rows:
            self.out.write(f"\x1b[{self.rows}A\r\x1b[J")
        self.out.write("\n".join(lines) + "\n")
        self.out.flush()
        self.size = (size.columns, size.lines)
        self.rows = present.frame_rows(lines, size.columns)


class _Resized(Exception):
    pass


_winch = {"idle": False, "pending": False}


def _on_winch(_signum, _frame) -> None:
    _winch["pending"] = True
    if _winch["idle"]:  # only ever raised while blocked in _wait, so no key is half read
        _winch["idle"] = False
        raise _Resized


def _wait(stdin: IO[str]) -> bool:
    """Block until a key is ready (False) or the terminal was resized (True): redraw then."""
    try:
        _winch["idle"] = True  # armed first, then checked: a signal in between is not lost
        if _winch["pending"]:
            raise _Resized
        select.select([stdin], [], [])
    except _Resized:
        _winch["pending"] = False
        return True
    except (OSError, ValueError, TypeError):  # not a real file (tests): just read
        pass
    finally:
        _winch["idle"] = False
    return False


@contextmanager
def _owns_screen(out: IO[str]):
    """Hide the cursor and redraw on resize while a panel is up; restore both on exit."""
    out.write("\x1b[?25l")
    try:
        previous = signal.signal(signal.SIGWINCH, _on_winch)
    except (AttributeError, ValueError):  # no SIGWINCH / not the main thread
        previous = None
    try:
        yield
    finally:
        if previous is not None:
            signal.signal(signal.SIGWINCH, previous)
        out.write("\x1b[?25h")
        out.flush()


def _confirm(state: State, screen: _Screen, render: Callable[[], list[str]],
             stdin: IO[str], question: str) -> bool:
    state.note = ("input", question)
    screen.draw(render())
    while _wait(stdin):
        screen.draw(render())
    if keyreader.read_key(stdin) == "yes":
        return True
    state.note = ("info", t("tui.cancelled"))
    return False


def _vacation_tui(state: State, stdin: IO[str], screen: _Screen) -> None:
    selected, editing = 0, None
    state.note = ("", "")
    while True:
        screen.draw(_vacation_lines(state, selected, screen.out, editing))
        if _wait(stdin):
            continue  # resized: redraw at the top of the loop
        if editing is not None:
            key = keyreader.read_edit_key(stdin)
            if key == "escape":
                editing, state.draft, state.note = None, "", ("", "")
            elif key == "enter":
                if editing.strip():
                    state.draft = editing  # kept so a rejected entry can be fixed, not retyped
                    if _vacation_add(state, editing):
                        state.draft = ""
                editing = None
            elif key == "backspace":
                editing = editing[:-1]
            elif key and len(editing) < EDIT_MAX:
                editing += key
            continue
        key = keyreader.read_key(stdin)
        count = len(state.settings.vacation) + 1
        if key in ("esc", "quit", "left"):
            state.note = ("", "")
            return
        if key in ("up", "down"):
            selected = (selected + (1 if key == "down" else -1)) % count
            state.note = ("", "")
        elif key == "add" or (key in ("enter", "space", "right") and selected == 0):
            editing, state.note = state.draft, ("", "")
        elif key in ("enter", "space", "delete") and selected > 0:
            _vacation_remove(state, state.settings.vacation[selected - 1])
            selected = min(selected, len(state.settings.vacation))


def _tui(state: State, stdin: IO[str], out: IO[str]) -> int:
    selected = 0
    screen = _Screen(out)
    state.defer = True

    def render() -> list[str]:
        return _main_lines(state, selected, out)

    try:
        with _owns_screen(out), keyreader.key_session(stdin):
            screen.draw(render())
            while True:
                if _wait(stdin):
                    screen.draw(render())
                    continue
                key = keyreader.read_key(stdin)
                if key == "other":
                    continue
                row = ROWS[selected]
                if key in ("up", "down"):
                    selected = (selected + (1 if key == "down" else -1)) % len(ROWS)
                    state.note = ("", "")
                adjusting = key in ("left", "right", "enter", "space") and row.kind in ADJUSTABLE
                synced = False if adjusting else _settle(state)  # leaving the row
                if key in ("quit", "esc"):
                    if synced:
                        screen.draw(render())  # show the sync result before leaving
                    break
                if adjusting:
                    _commit(state, _step(state.settings, row, -1 if key == "left" else 1))
                elif key in ("left", "right", "enter", "space"):
                    if row.kind == "vacation":
                        _vacation_tui(state, stdin, screen)
                        _settle(state)
                    elif row.kind in TYPED:
                        _typed_tui(state, row, stdin, screen, render)
                    else:
                        state.note = ("info", t("tui.readonly"))
                elif key == "reset":
                    if not _resettable(row):
                        state.note = ("info", t("tui.nothing_to_reset"))
                    elif _confirm(state, screen, render, stdin, _row_question(state, row)):
                        _reset_row(state, row)
                        _settle(state)
                elif key == "reset_all":
                    if _confirm(state, screen, render, stdin, t("tui.prompt.confirm_all")):
                        _reset_all(state)
                        _settle(state)
                screen.draw(render())
    except KeyboardInterrupt:
        _settle(state)  # a saved schedule change must still reach launchd
    return 1 if state.failed else 0


# ── numbered fallback ────────────────────────────────────────────────────────


def _ask(prompt: str, stdin: IO[str], out: IO[str]) -> str | None:
    print(prompt, file=out)
    out.flush()
    return keyreader.read_line(stdin)


def _yes(answer: str | None) -> bool:
    return (answer or "").strip().lower() in ("y", "yes")


def _numbered_row(state: State, row: Row, stdin: IO[str], out: IO[str]) -> None:
    if row.kind == "install":
        state.note = ("info", t("tui.readonly"))
    elif row.kind == "vacation":
        _vacation_numbered(state, stdin, out)
    elif row.kind in ("toggle", "language"):
        _commit(state, _step(state.settings, row, 1))
    elif row.kind == "text":
        answer = _ask(t("tui.prompt.text_ask", label=t(f"tui.row.{row.key}"),
                        current=_value(state, row)), stdin, out)
        _apply_typed(state, row, answer or "")
    elif row.kind == "secret":
        # No echo: read_hidden refuses rather than falling back to a visible prompt, so a
        # piped fallback cannot put a token on the scrollback.
        answer = telegram.read_token(t("tui.prompt.secret_enter"))
        if answer is None:
            state.note = ("error", t("tui.error.no_echo"))
        else:
            _apply_typed(state, row, answer)
    elif row.kind == "clock":
        low, high = config.clock(row.low), config.clock(row.high)
        answer = _ask(t("tui.prompt.value", label=t(f"tui.row.{row.key}"),
                        current=_value(state, row), low=low, high=high), stdin, out)
        if not answer:
            return
        hours, _, minutes = answer.strip().partition(":")
        value = int(hours) * 60 + int(minutes) if hours.isdecimal() and minutes.isdecimal() else -1
        if not config.valid_overtime(value):
            state.note = ("error", t("tui.error.clock", low=low, high=high))
            return
        _commit(state, _with(state.settings, row, value))
    else:
        low, high = _bounds(row, state.settings)
        answer = _ask(t("tui.prompt.value", label=t(f"tui.row.{row.key}"),
                        current=_value(state, row), low=low, high=high), stdin, out)
        if not answer:
            return
        try:
            number = int(answer)
        except ValueError:
            state.note = ("error", t("tui.error.number", low=low, high=high))
            return
        if row.kind == "interval":  # clamped, not rejected: the window decides the ceiling
            number = min(max(number, low), high)
        elif not low <= number <= high:
            state.note = ("error", t("tui.error.number", low=low, high=high))
            return
        _commit(state, _with(state.settings, row, number))


def _vacation_numbered(state: State, stdin: IO[str], out: IO[str]) -> None:
    state.note = ("", "")
    while True:
        for line in _vacation_lines(state, None, out):
            print(line, file=out)
        answer = _ask(t("tui.prompt.choice"), stdin, out)
        if not answer:
            state.note = ("", "")
            return
        command, _, arg = answer.partition(" ")
        if command.lower() == "a" and arg.strip():
            _vacation_add(state, arg)
        elif command.lower() == "d" and arg.strip():
            _vacation_remove(state, arg)
        else:
            state.note = ("error", t("tui.error.vacation_command"))


def _numbered(state: State, stdin: IO[str], out: IO[str]) -> int:
    """Typed-choice surface. EOF or q is quit - never a block, so CI is safe."""
    while True:
        for line in _main_lines(state, None, out):
            print(line, file=out)
        choice = _ask(t("tui.prompt.choice"), stdin, out)
        state.note = ("", "")
        if choice is None or choice.lower() in ("", "q"):
            return 1 if state.failed else 0
        number = choice[1:].strip() if choice[:1] == "r" else choice
        if choice == "R":
            if _yes(_ask(t("tui.prompt.confirm_all"), stdin, out)):
                _reset_all(state)
            else:
                state.note = ("info", t("tui.cancelled"))
        elif number.isdecimal() and 1 <= int(number) <= len(ROWS):
            row = ROWS[int(number) - 1]
            if choice[:1] != "r":
                _numbered_row(state, row, stdin, out)
            elif not _resettable(row):
                state.note = ("info", t("tui.nothing_to_reset"))
            elif _yes(_ask(_row_question(state, row), stdin, out)):
                _reset_row(state, row)
            else:
                state.note = ("info", t("tui.cancelled"))
        else:
            state.note = ("error", t("tui.error.choice"))

# ── update prompt ────────────────────────────────────────────────────────────

# Rows top to bottom: the answers update_check.offer acts on.
_UPDATE_ANSWERS = (update_check.UPDATE_NOW, update_check.SKIP, update_check.SKIP_VERSION)


def _update_lines(found: update_check.UpdateAvailable, notes_url: str, selected: int,
                  out: IO[str], style: present.Style) -> list[str]:
    """The update prompt in the --config panel look: amber frame, pointer row, teal detail."""
    choices = (("update.now", "update.now_detail"), ("update.skip", "update.skip_detail"),
               ("update.skip_version", "update.skip_version_detail"))
    items = [(f"{i}) {t(name)}", t(detail), "value") for i, (name, detail) in enumerate(choices, 1)]
    return present.compose(
        t("update.available", latest=found.latest, current=found.current), items, selected,
        columns=_terminal_size(out).columns, lines=None, style=style,
        hint=t("update.keys"), help_text=t("update.notes", url=notes_url),
    )


def update_prompt(found: update_check.UpdateAvailable, notes_url: str, *,
                  stdin: IO[str], out: IO[str]) -> str:
    """The ``ask`` callback for update_check.offer: returns UPDATE_NOW / SKIP / SKIP_VERSION and
    does nothing else. q or EOF is SKIP."""
    style = present.Style(present.color_enabled(out), present.pointer(os.environ, out))
    selected = 0
    screen = _Screen(out)

    def draw() -> None:
        screen.draw(_update_lines(found, notes_url, selected, out, style))

    with _owns_screen(out), keyreader.key_session(stdin):
        draw()
        while True:
            if _wait(stdin):
                draw()
                continue
            key = keyreader.read_key(stdin)
            if key in ("quit", "esc"):
                return update_check.SKIP
            if key in ("enter", "space"):
                return _UPDATE_ANSWERS[selected]
            if key not in ("up", "down"):
                continue
            selected = (selected + (1 if key == "down" else -1)) % len(_UPDATE_ANSWERS)
            draw()


def config_menu(stdin: IO[str] | None = None, stdout: IO[str] | None = None,
                status_lines: Callable[[], list[str]] | None = None) -> int:
    """Run the settings menu; 0 on a clean exit, 1 when a save or sync failed."""
    stdin = stdin or sys.stdin
    out = stdout or sys.stdout
    if hasattr(out, "reconfigure"):  # a non-UTF-8 locale shows "?" for glyphs instead of crashing
        out.reconfigure(errors="replace")
    state = State(config.load(), status_lines)
    i18n.set_language(state.settings.language)
    if backup := config.corrupt_backup():  # shown on the first draw; the next key clears it
        home = str(Path.home())
        shown = str(backup).replace(home, "~", 1) if str(backup).startswith(home) else str(backup)
        state.note = ("info", t("tui.corrupt_backup", path=shown))
    if keyreader.is_interactive(stdin, out):
        state.pointer = present.pointer(os.environ, out)
        return _tui(state, stdin, out)
    return _numbered(state, stdin, out)
