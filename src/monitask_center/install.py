"""First-time setup: runtime, PyObjC, Telegram, launchd.

Port of the old setup.sh. Idempotent: a repeat run only repairs what is broken, so the
MonitaskCenter.app identity survives. Never controls Monitask.
"""

from __future__ import annotations

import getpass
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from . import config, launchd, telegram
from .i18n import t

BREW_FALLBACKS = ("/opt/homebrew/bin/brew", "/usr/local/bin/brew")
FORMULA = "python@3.13"
# telegram-kit rides along with PyObjC so launchd's bare python finds it on PYTHONPATH:
# run.sh exports src:pylibs, not the uv venv the CLI runs from.
PIP_PACKAGES = ("pyobjc-framework-Cocoa", "pyobjc-framework-ApplicationServices",
                "pyobjc-framework-Quartz", "telegram-kit>=0.2.0,<0.3")
IMPORT_PROBE = "import AppKit, ApplicationServices, Foundation, Quartz, telegram_kit"
TOKEN_RE = telegram.TOKEN_RE
CHAT_RE = telegram.CHAT_RE
ENV_KEYS = telegram.ENV_KEYS


class InstallError(Exception):
    """The message is already user-facing text."""


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _say(step: str) -> None:
    print(f"\n==> {t(step)}")


def _default_confirm(question: str, input_fn=input) -> bool:
    while True:
        answer = input_fn(f"{question} [Y/n] ").strip().lower() or "y"
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print(t("install.confirm.retry"))


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, text=True, check=False, **kw)
    except OSError as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def _check_run(cmd: list[str], **kw) -> None:
    if _run(cmd, **kw).returncode != 0:
        raise InstallError(t("install.err.command_failed", command=" ".join(cmd[:3])))


class Paths:
    def __init__(self) -> None:
        self.root = config.RUNNER.parent
        self.runner_sh = config.RUNNER
        self.app = self.root / "MonitaskCenter.app"
        self.python_runner = self.app / "Contents/MacOS/python-runner"
        self.pylibs = self.root / "pylibs"
        self.pyfix = self.root / "pyfix"
        self.env = self.root / ".env"


def _find_brew() -> str:
    found = shutil.which("brew")
    for candidate in (found, *BREW_FALLBACKS):
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    raise InstallError(t("install.err.no_brew"))


def _brew_python(brew: str) -> Path:
    r = _run([brew, "--prefix", FORMULA], capture_output=True)
    prefix = (r.stdout or "").strip()
    if r.returncode != 0 or not prefix:
        raise InstallError(t("install.err.brew_prefix"))
    return Path(prefix) / "bin/python3.13"


def _version(binary: Path) -> str | None:
    """`binary --version`, or None when it is missing or cannot run (a dead runner is not a match)."""
    r = _run([str(binary), "--version"], capture_output=True)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    return out if r.returncode == 0 and out else None


def _probe_imports(p: Paths) -> bool:
    env = {**os.environ, "PYTHONPATH": str(p.pylibs)}
    return _run([str(p.python_runner), "-c", IMPORT_PROBE], env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def prepare_runtime(p: Paths) -> None:
    brew = _find_brew()
    python = _brew_python(brew)
    if not os.access(python, os.X_OK):
        _check_run([brew, "install", FORMULA])
        python = _brew_python(brew)
        if not os.access(python, os.X_OK):
            raise InstallError(t("install.err.python_missing", path=python))
    # Keep an unchanged bundle identity so repeat installs retain Accessibility approval.
    # Downloaded checkouts can quarantine this locally signed bundle; clear only its
    # download flag before any runner probe, leaving Gatekeeper enabled system-wide.
    _check_run(["/usr/bin/xattr", "-dr", "com.apple.quarantine", str(p.app)])
    wanted = _version(python)
    if wanted is None or _version(p.python_runner) != wanted:
        p.python_runner.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(python, p.python_runner)
        _check_run(["codesign", "--force", "--sign", "-", str(p.app)])
        print(t("install.runtime.rebuilt"))
    if not _probe_imports(p):
        _check_run([str(python), "-m", "pip", "install", "--upgrade", "--target", str(p.pylibs),
                    "--only-binary=:all:", *PIP_PACKAGES], env={**os.environ, "PYTHONPATH": str(p.pyfix)})
    if not _probe_imports(p):
        raise InstallError(t("install.err.imports"))
    print(t("install.runtime.ok"))


def _store_token(p: Paths, value: str) -> bool:
    """Persist the bot token in the credential store, falling back to *.env* without one.

    The store is the mechanism ``--config`` uses too; *.env* is only reached when the
    machine has no store to write to, so a token is never silently left in plaintext on a
    machine that could have kept it out of the config file.
    """
    if telegram.set_token(value):
        return True
    lines = telegram.env_lines(p.env)
    body = f"{telegram.ENV_KEYS[0]}={value}\n"
    chat = telegram.env_value(lines, telegram.ENV_KEYS[1]).strip()
    if chat:
        body += f"{telegram.ENV_KEYS[1]}={chat}\n"
    telegram.write_env(p.env, telegram.strip_env_keys(lines), body)
    print(t("install.tg.saved_env", backend=telegram.backend_label()))
    return True


def configure_telegram(p: Paths, settings: config.Settings, *, getpass_fn, input_fn, confirm) -> None:
    if not settings.telegram:
        return print(t("install.tg.disabled"))
    # A token already sitting in .env moves into the credential store, so the next run has
    # one mechanism rather than two. A store that already holds one wins and .env is left be.
    if telegram.migrate_env_token(p.env):
        print(t("install.tg.migrated"))
    token, chat = telegram.resolve(settings.telegram_chat_id)
    if TOKEN_RE.match(token) and CHAT_RE.match(chat):
        print(t("install.tg.valid", backend=telegram.backend_label()))
        return _send_test(settings)
    if not confirm(t("install.tg.ask")):
        return print(t("install.tg.declined"))
    if TOKEN_RE.match(token):
        print(t("install.tg.token_kept"))
    else:
        print(t("install.tg.need_token"))
        token = getpass_fn(t("install.tg.prompt_token")).strip()
        if not TOKEN_RE.match(token):
            raise InstallError(t("install.err.token_format"))
        _store_token(p, token)
    if CHAT_RE.match(chat):
        print(t("install.tg.chat_kept"))
    else:
        chat = input_fn(t("install.tg.prompt_chat")).strip()
        if not CHAT_RE.match(chat):
            raise InstallError(t("install.err.chat_format"))
    if settings.telegram_chat_id != chat:  # the chat id is not a secret: it lives in config.json
        config.save(config.validate(replace(settings, telegram_chat_id=chat)))
    print(t("install.tg.saved"))
    _send_test(replace(settings, telegram_chat_id=chat))


def _send_test(settings: config.Settings) -> None:
    """Fire one test message; delivery is not checked, the user confirms it on Telegram."""
    telegram.send(t("install.tg.test_message"), settings.telegram_chat_id)
    print(t("install.tg.test_sent"))


def run_install(settings: config.Settings, *, input_fn=input, getpass_fn=getpass.getpass,
                confirm=None) -> int:
    """0 on success; non-zero (message on stderr) on any failure, before launchd when it is a prerequisite."""
    confirm = confirm or (lambda q: _default_confirm(q, input_fn))
    try:
        if not _is_macos():
            raise InstallError(t("install.err.macos_only"))
        p = Paths()
        _say("install.step.files")
        for path in (p.runner_sh, p.app / "Contents/Info.plist"):
            if not path.is_file():
                raise InstallError(t("install.err.missing_file", path=path))
        print(t("install.files.ok"))
        _say("install.step.runtime")
        prepare_runtime(p)
        _say("install.step.telegram")
        configure_telegram(p, settings, getpass_fn=getpass_fn, input_fn=input_fn, confirm=confirm)
        _say("install.step.launchd")
        try:
            launchd.install(settings)
        except (subprocess.CalledProcessError, OSError) as exc:
            raise InstallError(t("install.err.launchd", error=launchd.describe_error(exc))) from exc
        _say("install.step.verify")
        installed, count, total = launchd.is_installed(), len(launchd.loaded()), len(launchd.JOBS)
        print(t("install.verify.installed", mark="✅" if installed else "❌",
                state=t("install.verify.yes" if installed else "install.verify.no")))
        print(t("install.verify.loaded", mark="✅" if count == total else "❌", count=count, total=total))
        if not installed or count != total:
            raise InstallError(t("install.err.verify"))
    except InstallError as exc:
        print(t("install.error", message=exc), file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\n" + t("install.error", message=t("install.err.aborted")), file=sys.stderr)
        return 1
    print("\n" + t("install.done"))
    print(t("install.done.hint"))
    return 0


def uninstall() -> int:
    removed = launchd.uninstall()
    print(t("install.uninstall.done", jobs=", ".join(removed)) if removed else t("install.uninstall.none"))
    return 0
