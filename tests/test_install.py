"""Installer tests: tmp project/HOME and a fake subprocess layer; nothing real is ever run."""

import os
import stat
import subprocess
from pathlib import Path

import pytest

from monitask_center import config, install, launchd
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


def run(**kw):
    kw.setdefault("input_fn", lambda prompt="": "")
    kw.setdefault("confirm", lambda q: False)
    return install.run_install(Settings(), **kw)


def test_fresh_install_bootstraps_one_job_and_keeps_env(system, tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_bytes(b"OTHER_SETTING=preserved\n")
    assert run() == 0
    assert system.count("loaded") == 1 and launchd.plist_path("check").exists()
    assert (system.count("brew"), system.count("pip"), system.count("codesign")) == (1, 1, 1)
    assert system.runner.read_text() == PYTHON_VERSION
    assert env.read_bytes() == b"OTHER_SETTING=preserved\n"  # no tg-send.sh -> untouched
    out = capsys.readouterr().out
    assert t("install.done") in out and "✅ 已載入的排程:1/1" in out and "❌" not in out


def test_verify_failure_is_an_error_not_done(system, monkeypatch, capsys):
    monkeypatch.setattr(launchd, "loaded", lambda: [])
    assert run() == 1
    captured = capsys.readouterr()
    assert "❌ 已載入的排程:0/1" in captured.out and t("install.done") not in captured.out
    assert t("install.err.verify") in captured.err


def test_repeat_install_only_reloads_launchd(system):
    assert run() == 0
    assert run() == 0
    assert (system.count("brew"), system.count("pip"), system.count("codesign")) == (1, 1, 1)
    assert system.count("loaded") == 2


@pytest.mark.parametrize("broken", ["dyld: cannot load libpython", "Python 3.12.0"])
def test_broken_or_outdated_runner_is_rebuilt(system, broken):
    assert run() == 0
    system.runner.write_text(broken)  # not-working runner (and a plain version mismatch)
    assert run() == 0
    assert system.count("codesign") == 2
    assert system.runner.read_text() == PYTHON_VERSION
    assert system.count("pip") == 1  # deps probe passes again once the runner works


def test_telegram_saved_hidden_preserved_and_private(system, tmp_path, capsys, monkeypatch):
    helper = Path.home() / ".claude/scripts/tg-send.sh"
    helper.parent.mkdir(parents=True)
    helper.write_text("")
    env = tmp_path / ".env"
    env.write_text("OTHER_SETTING=preserved\nTG_CHAT_ID=bad")  # no trailing newline, stale bad chat id
    answers = iter(["-123"])
    assert run(getpass_fn=lambda prompt="": TOKEN, input_fn=lambda prompt="": next(answers),
               confirm=lambda q: True) == 0
    lines = env.read_text().splitlines()
    assert lines[0] == "OTHER_SETTING=preserved"
    assert sorted(lines[1:]) == [f"TG_BOT_TOKEN={TOKEN}", "TG_CHAT_ID=-123"]
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err
    assert system.count("tg-send") == 1 and t("install.tg.test_sent") in captured.out
    assert not list(tmp_path.glob(".env.setup.*"))
    # An already-valid config is kept byte-identical and never re-prompted.
    before = env.read_bytes()
    env.chmod(0o644)
    boom = lambda *a, **k: pytest.fail("prompted")  # noqa: E731
    assert run(getpass_fn=boom, input_fn=boom, confirm=boom) == 0
    assert env.read_bytes() == before and stat.S_IMODE(env.stat().st_mode) == 0o600
    assert system.count("tg-send") == 2  # a kept config is test-sent too
    assert TOKEN not in "".join(capsys.readouterr())


def test_invalid_token_fails_before_launchd(system, tmp_path, capsys):
    helper = Path.home() / ".claude/scripts/tg-send.sh"
    helper.parent.mkdir(parents=True)
    helper.write_text("")
    assert run(getpass_fn=lambda prompt="": "not a token", confirm=lambda q: True) == 1
    assert system.count("loaded") == 0 and not (tmp_path / ".env").exists()
    assert "not a token" not in capsys.readouterr().err


def test_untrusted_accessibility_fails_before_launchd(system, capsys):
    system.trusted = False
    assert run() == 1
    assert system.count("loaded") == 0 and not launchd.plist_path("check").exists()
    assert system.count("open") == 0
    captured = capsys.readouterr()
    assert t("install.err.accessibility") in captured.err and t("install.done") not in captured.out


def test_accessibility_pane_opened_on_request_and_granted_on_recheck(system):
    system.trusted = False
    asked = []

    def enter(prompt=""):
        system.trusted = True  # user flips the switch while the prompt waits
        asked.append(prompt)
        return ""

    assert run(confirm=lambda q: True, input_fn=enter) == 0
    assert system.count("open") == 1 and system.count("loaded") == 1


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
