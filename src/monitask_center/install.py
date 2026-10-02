"""First-time setup (stdlib only): runtime, PyObjC, optional Telegram, Accessibility, launchd.

Port of the old setup.sh. Idempotent: a repeat run only repairs what is broken, so the
MonitaskCenter.app identity (and its Accessibility grant) survives. Never controls Monitask.
"""

from __future__ import annotations

import getpass
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import config, launchd
from .i18n import t
from .status import parse_kv, run_status

BREW_FALLBACKS = ("/opt/homebrew/bin/brew", "/usr/local/bin/brew")
FORMULA = "python@3.13"
PIP_PACKAGES = ("pyobjc-framework-Cocoa", "pyobjc-framework-ApplicationServices", "pyobjc-framework-Quartz")
IMPORT_PROBE = "import AppKit, ApplicationServices, Foundation, Quartz"
AX_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
TOKEN_RE = re.compile(r"^[0-9]+:[A-Za-z0-9_-]+$")
CHAT_RE = re.compile(r"^-?[0-9]+$")
ENV_KEYS = ("TG_BOT_TOKEN", "TG_CHAT_ID")


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


def _env_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError:
        return []


def _env_value(lines: list[str], key: str) -> str:
    for line in lines:
        if line.startswith(f"{key}="):
            return line[len(key) + 1:].rstrip("\r\n")
    return ""


def configure_telegram(p: Paths, settings: config.Settings, *, getpass_fn, input_fn, confirm) -> None:
    helper = Path.home() / ".claude/scripts/tg-send.sh"
    if not helper.exists():
        return print(t("install.tg.unavailable"))
    if not settings.telegram:
        return print(t("install.tg.disabled"))
    lines = _env_lines(p.env)
    token, chat = (_env_value(lines, k) for k in ENV_KEYS)
    token_ok, chat_ok = bool(TOKEN_RE.match(token)), bool(CHAT_RE.match(chat))
    if token_ok and chat_ok:
        os.chmod(p.env, 0o600)
        print(t("install.tg.valid"))
        return _send_test(p, helper)
    if not confirm(t("install.tg.ask")):
        return print(t("install.tg.declined"))
    if token_ok:
        print(t("install.tg.token_kept"))
    else:
        print(t("install.tg.need_token"))
        token = getpass_fn(t("install.tg.prompt_token")).strip()
        if not TOKEN_RE.match(token):
            raise InstallError(t("install.err.token_format"))
    if chat_ok:
        print(t("install.tg.chat_kept"))
    else:
        chat = input_fn(t("install.tg.prompt_chat")).strip()
        if not CHAT_RE.match(chat):
            raise InstallError(t("install.err.chat_format"))
    kept = [ln for ln in lines if not ln.startswith(tuple(f"{k}=" for k in ENV_KEYS))]
    if kept and not kept[-1].endswith("\n"):
        kept[-1] += "\n"
    body = "".join(kept) + f"TG_BOT_TOKEN={token}\nTG_CHAT_ID={chat}\n"
    old_mask = os.umask(0o077)
    try:
        fd, tmp = tempfile.mkstemp(dir=p.root, prefix=".env.setup.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(body)
            os.chmod(tmp, 0o600)
            os.replace(tmp, p.env)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    finally:
        os.umask(old_mask)
    print(t("install.tg.saved"))
    _send_test(p, helper)


def _send_test(p: Paths, helper: Path) -> None:
    """Fire one test message; delivery is not checked, the user confirms it on Telegram."""
    try:
        _run(["/bin/bash", str(helper), "send", t("install.tg.test_message")], cwd=p.root,
             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
    except subprocess.TimeoutExpired:
        pass
    print(t("install.tg.test_sent"))


def _trusted() -> bool:
    _, out = run_status()
    print(out.rstrip())
    return parse_kv(out).get("accessibility_trusted") == "True"


def check_accessibility(*, input_fn, confirm) -> None:
    if _trusted():
        return print(t("install.ax.ok"))
    print("\n" + t("install.ax.needed"))
    if confirm(t("install.ax.open_ask")):
        _run(["open", AX_PANE])
    input_fn(t("install.ax.press_enter"))
    if not _trusted():
        raise InstallError(t("install.err.accessibility"))


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
        _say("install.step.accessibility")
        check_accessibility(input_fn=input_fn, confirm=confirm)
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
