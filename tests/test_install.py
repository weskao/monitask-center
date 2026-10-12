"""Installer tests: tmp project/HOME and a fake subprocess layer; nothing real is ever run."""

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from monitask_center import config, install, launchd, telegram
from monitask_center.config import Settings
from monitask_center.i18n import t

TOKEN = "123:synthetic_fixture"
PYTHON_VERSION = "Python 3.13.15"


class FakeSystem:
    """Stands in for brew / codesign / launchctl / open / python / python-runner / run.sh."""

    def __init__(self, tmp_path: Path):
        self.root = tmp_path  # == config.RUNNER.parent
        self.python = tmp_path / "brew-prefix/bin/python3.13"
        self.runner = tmp_path / "MonitaskCenter.app/Contents/MacOS/python-runner"
        self.calls: list[str] = []
        self.deps = False
        self.trusted = True
        self.bootstrap_fails = False
        self.stderr = ""
        self.pip_env: dict = {}

    def count(self, name: str) -> int:
        return self.calls.count(name)

    def _version(self, binary: Path) -> subprocess.CompletedProcess:
        text = binary.read_text() if binary.exists() else ""
        ok = text.startswith("Python")
        return subprocess.CompletedProcess([], 0 if ok else 1, text if ok else "", "" if ok else "dyld: libpython")

    def run(self, cmd, check=False, **kw):
        name, args = os.path.basename(cmd[0]), list(cmd[1:])
        rc, out = 0, ""
        if name == "brew" and args[0] == "--prefix":
            out = f"{self.python.parent.parent}\n"
        elif name == "brew":
            self.calls.append("brew")
            self.python.parent.mkdir(parents=True, exist_ok=True)
            self.python.write_text(PYTHON_VERSION)
            self.python.chmod(0o755)
        elif name == "codesign":
            self.calls.append("codesign")
        elif name == "xattr":
            self.calls.append("xattr")
        elif name == "open":
            self.calls.append("open")
        elif name == "launchctl" and args[0] == "bootstrap":
            if self.bootstrap_fails:
                rc, self.stderr = 5, "Bootstrap failed: 5: Input/output error\nsecond line\n"
            else:
                self.calls.append("loaded")
        elif name == "launchctl" and args[0] == "list":
            out = "".join(f"-\t0\t{launchd.label(j)}\n" for j in launchd.JOBS if launchd.plist_path(j).exists())
        elif name == "launchctl":
            pass  # bootout
        elif name == "bash" and args[0].endswith("tg-send.sh"):
            self.calls.append(f"tg-{args[1]}")
        elif name == "bash":  # run.sh status -> the runner must work
            r = self._version(self.runner)
            if r.returncode:
                rc, out = 1, r.stderr
            else:
                out = f"accessibility_trusted={self.trusted}\nrunning=False\nstate=not-running\ngui_session=True\n"
        elif name in ("python3.13", "python-runner") and args == ["--version"]:
            r = self._version(Path(cmd[0]))
            rc, out = r.returncode, r.stdout
        elif name == "python-runner" and args[:1] == ["-c"]:
            rc = 0 if self.deps and self._version(self.runner).returncode == 0 else 1
        elif name == "python3.13" and args[:3] == ["-m", "pip", "install"]:
            self.calls.append("pip")
            self.pip_env = kw.get("env", {})
            assert "--only-binary=:all:" in args and "--target" in args and "--upgrade" in args
            self.deps = True
        else:
            raise AssertionError(f"unexpected command {cmd}")
        proc = subprocess.CompletedProcess(cmd, rc, out, self.stderr if rc else "")
        if check and rc:
            raise subprocess.CalledProcessError(rc, cmd)
        return proc


@pytest.fixture
def system(tmp_path, monkeypatch):
    fake = FakeSystem(tmp_path)
    (tmp_path / "MonitaskCenter.app/Contents").mkdir(parents=True)
    (tmp_path / "MonitaskCenter.app/Contents/Info.plist").write_text("<plist/>")
    (tmp_path / "bin").mkdir()
    brew = tmp_path / "bin/brew"
    brew.write_text("#!/bin/sh\n")
    brew.chmod(0o755)
    monkeypatch.setattr(subprocess, "run", fake.run)
    monkeypatch.setattr(install.shutil, "which", lambda name: str(brew))
    monkeypatch.setattr(install, "_is_macos", lambda: True)
    return fake


def run(settings=None, **kw):
    kw.setdefault("input_fn", lambda prompt="": "")
    kw.setdefault("confirm", lambda q: False)
    # A safety net: without this a test that confirms the Telegram step would block on the
    # real terminal waiting for a secret.
    kw.setdefault("getpass_fn", lambda prompt="": "")
    return install.run_install(settings or Settings(), **kw)


def test_fresh_install_bootstraps_every_job_and_keeps_env(system, tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_bytes(b"OTHER_SETTING=preserved\n")
    assert run() == 0
    assert system.count("loaded") == len(launchd.JOBS) and launchd.plist_path("check").exists()
    assert (system.count("brew"), system.count("pip"), system.count("codesign")) == (1, 1, 1)
    assert system.runner.read_text() == PYTHON_VERSION
    assert env.read_bytes() == b"OTHER_SETTING=preserved\n"  # no tg-send.sh -> untouched
    out = capsys.readouterr().out
    assert t("install.done") in out and "✅ 已載入的排程:2/2" in out and "❌" not in out


def test_verify_failure_is_an_error_not_done(system, monkeypatch, capsys):
    monkeypatch.setattr(launchd, "loaded", lambda: [])
    assert run() == 1
    captured = capsys.readouterr()
    assert "❌ 已載入的排程:0/2" in captured.out and t("install.done") not in captured.out
    assert t("install.err.verify") in captured.err


def test_repeat_install_only_reloads_launchd(system):
    assert run() == 0
    assert run() == 0
    assert (system.count("brew"), system.count("pip"), system.count("codesign")) == (1, 1, 1)
    assert system.count("loaded") == 2 * len(launchd.JOBS)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS quarantine attributes")
def test_runtime_clears_bundle_quarantine_before_probing_without_resigning(tmp_path, monkeypatch):
    p = install.Paths()
    p.python_runner.parent.mkdir(parents=True)
    info = p.app / "Contents/Info.plist"
    info.write_text("<plist/>")
    p.python_runner.write_text(PYTHON_VERSION)
    targets = (p.app, info, p.python_runner)
    for path in targets:
        subprocess.run(["/usr/bin/xattr", "-w", "com.apple.quarantine",
                        "0087;00000000;TestDownload;", str(path)], check=True)

    monkeypatch.setattr(install, "_find_brew", lambda: "brew")
    monkeypatch.setattr(install, "_brew_python", lambda brew: Path(sys.executable))
    monkeypatch.setattr(install, "_probe_imports", lambda paths: True)

    def version(binary):
        for path in targets:
            attrs = subprocess.run(["/usr/bin/xattr", str(path)], capture_output=True,
                                   text=True, check=True).stdout.splitlines()
            assert "com.apple.quarantine" not in attrs
        return PYTHON_VERSION

    monkeypatch.setattr(install, "_version", version)
    install.prepare_runtime(p)
    install.prepare_runtime(p)  # absent attributes are harmless on repeat installs
    assert p.python_runner.read_text() == PYTHON_VERSION


@pytest.mark.parametrize("broken", ["dyld: cannot load libpython", "Python 3.12.0"])
def test_broken_or_outdated_runner_is_rebuilt(system, broken):
    assert run() == 0
    system.runner.write_text(broken)  # not-working runner (and a plain version mismatch)
    assert run() == 0
    assert system.count("codesign") == 2
    assert system.runner.read_text() == PYTHON_VERSION
    assert system.count("pip") == 1  # deps probe passes again once the runner works


def test_telegram_token_goes_to_the_credential_store_and_the_chat_id_to_config(
        system, tmp_path, capsys, monkeypatch, no_real_keychain):
    env = tmp_path / ".env"
    env.write_text("OTHER_SETTING=preserved\n")  # unrelated entries must survive untouched
    sent = []
    monkeypatch.setattr(telegram, "send", lambda text, chat_id="": sent.append((text, chat_id)) or True)
    answers = iter(["-123"])
    assert run(getpass_fn=lambda prompt="": TOKEN, input_fn=lambda prompt="": next(answers),
               confirm=lambda q: True) == 0
    assert no_real_keychain[telegram.TOKEN_KEY] == TOKEN  # the store, not the file
    assert env.read_text() == "OTHER_SETTING=preserved\n"  # no token left in plaintext
    assert config.load().telegram_chat_id == "-123"  # an identifier, so plain JSON
    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err  # never echoed
    assert sent and sent[0][1] == "-123" and t("install.tg.test_sent") in captured.out


def test_a_token_already_in_env_is_migrated_into_the_store(system, tmp_path, capsys, no_real_keychain):
    env = tmp_path / ".env"
    env.write_text(f"OTHER=keep\nTG_BOT_TOKEN={TOKEN}\n")
    assert run(confirm=lambda q: False) == 0  # declining the prompt still migrates
    assert no_real_keychain[telegram.TOKEN_KEY] == TOKEN
    assert env.read_text() == "OTHER=keep\n"  # the token is gone, the rest is intact
    assert t("install.tg.migrated") in capsys.readouterr().out


def test_without_a_credential_store_the_token_falls_back_to_env(
        system, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(telegram, "available", lambda: False)
    monkeypatch.setattr(telegram, "backend_label", lambda: "none")
    monkeypatch.setattr(telegram, "set_token", lambda v: False)
    monkeypatch.setattr(telegram, "token", lambda: "")
    env = tmp_path / ".env"
    answers = iter(["-123"])
    assert run(getpass_fn=lambda prompt="": TOKEN, input_fn=lambda prompt="": next(answers),
               confirm=lambda q: True) == 0
    # Only the secret needs the file fallback; the chat id is not a secret and goes to JSON.
    assert env.read_text() == f"{telegram.ENV_KEYS[0]}={TOKEN}\n"
    assert config.load().telegram_chat_id == "-123"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert t("install.tg.saved_env", backend="none") in capsys.readouterr().out


def test_a_valid_pair_is_kept_and_never_re_prompted(
        system, tmp_path, capsys, monkeypatch, no_real_keychain):
    no_real_keychain[telegram.TOKEN_KEY] = TOKEN
    config.save(Settings(telegram_chat_id="-123"))  # both halves already present
    env = tmp_path / ".env"
    env.write_bytes(b"OTHER=keep\n")
    boom = lambda *a, **k: pytest.fail("prompted")  # noqa: E731
    assert run(config.load(), getpass_fn=boom, input_fn=boom, confirm=boom) == 0
    assert env.read_text() == "OTHER=keep\n"  # nothing to migrate, nothing rewritten
    assert t("install.tg.test_sent") in capsys.readouterr().out


def test_a_malformed_token_is_rejected_not_stored(system, no_real_keychain):
    assert run(getpass_fn=lambda prompt="": "not-a-token", input_fn=lambda prompt="": "-1",
               confirm=lambda q: True) == 1
    assert telegram.TOKEN_KEY not in no_real_keychain  # refused, never half-saved


def test_invalid_token_fails_before_launchd(system, tmp_path, capsys):
    helper = Path.home() / ".claude/scripts/tg-send.sh"
    helper.parent.mkdir(parents=True)
    helper.write_text("")
    assert run(getpass_fn=lambda prompt="": "not a token", confirm=lambda q: True) == 1
    assert system.count("loaded") == 0 and not (tmp_path / ".env").exists()
    assert "not a token" not in capsys.readouterr().err


def test_install_without_accessibility_never_prompts_or_opens_settings(system, capsys):
    system.trusted = False

    def unexpected_input(prompt=""):
        pytest.fail("installation requested an Accessibility grant")

    assert run(input_fn=unexpected_input) == 0
    assert system.count("loaded") == len(launchd.JOBS) and launchd.plist_path("check").exists()
    assert system.count("open") == 0
    captured = capsys.readouterr()
    assert t("install.done") in captured.out and captured.err == ""


def test_bootstrap_failure_is_nonzero_without_completion_message(system, capsys):
    system.bootstrap_fails = True
    assert run() == 1
    captured = capsys.readouterr()
    assert t("install.done") not in captured.out and t("install.error", message="").strip() in captured.err
    assert "ERROR" not in captured.err


def test_bootstrap_failure_shows_one_localized_line_without_argv(system, capsys):
    system.bootstrap_fails = True
    assert run() == 1
    err = capsys.readouterr().err
    assert err.count("\n") == 1  # one line; launchctl's own stderr never leaks through
    assert "Bootstrap failed: 5: Input/output error" in err and "5" in err
    assert "second line" not in err and "launchctl" not in err.split("5", 1)[1]
    assert "[" not in err and "non-zero" not in err and "CalledProcessError" not in err
    assert err.strip() == t("install.error", message=t(
        "install.err.launchd", error=t("launchd.err.detail", code=5, detail="Bootstrap failed: 5: Input/output error")))


def test_guards(system, monkeypatch, capsys):
    monkeypatch.setattr(install, "_is_macos", lambda: False)
    assert run() == 1 and t("install.err.macos_only") in capsys.readouterr().err
    monkeypatch.setattr(install, "_is_macos", lambda: True)
    monkeypatch.setattr(install.shutil, "which", lambda name: None)
    monkeypatch.setattr(install, "BREW_FALLBACKS", ())
    assert run() == 1 and t("install.err.no_brew") in capsys.readouterr().err
    assert system.count("loaded") == 0


def test_uninstall_removes_job(system, capsys):
    assert run() == 0
    assert install.uninstall() == 0
    assert not launchd.plist_path("check").exists()
    assert install.uninstall() == 0
    assert t("install.uninstall.none") in capsys.readouterr().out
