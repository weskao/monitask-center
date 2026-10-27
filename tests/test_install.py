"""Installer tests: tmp project/HOME and a fake subprocess layer; nothing real is ever run."""

import os
import plistlib
import shutil
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
        self.tcc_unreadable = False
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
        elif name == "codesign" and args[0] == "-d":
            out = '# designated => cdhash H"5cfbbcd9"\n'
        elif name == "codesign":
            self.calls.append("codesign")
        elif name == "sqlite3":  # TCC.db: the grant row for the current (granted) or an old (stale) build
            rc = 1 if self.tcc_unreadable else 0
            out = {True: "2|FADE0C005CFBBCD9\n", "stale": "0|FADE0C00B23F50CE\n"}.get(self.trusted, "")
        elif name == "xattr":
            self.calls.append("xattr")
        elif name == "open":
            self.calls.append("open-pane" if args[0].startswith("x-apple") else "open")
        elif name == "tccutil":
            self.calls.append("tccutil")
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
    (tmp_path / "bin").mkdir()
    brew = tmp_path / "bin/brew"
    brew.write_text("#!/bin/sh\n")
    brew.chmod(0o755)
    monkeypatch.setattr(subprocess, "run", fake.run)
    monkeypatch.setattr(install.shutil, "which", lambda name: str(brew))
    monkeypatch.setattr(install, "_is_macos", lambda: True)
    return fake


def _env_file() -> Path:
    """The Telegram fallback .env, which lives with the settings now, not in a checkout."""
    path = config.home_dir() / ".env"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def run(settings=None, **kw):
    kw.setdefault("input_fn", lambda prompt="": "")
    kw.setdefault("confirm", lambda q: False)
    # A safety net: without this a test that confirms the Telegram step would block on the
    # real terminal waiting for a secret.
    kw.setdefault("getpass_fn", lambda prompt="": "")
    return install.run_install(settings or Settings(), **kw)


def test_fresh_install_bootstraps_every_job_and_keeps_env(system, tmp_path, capsys):
    env = _env_file()
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
    env = _env_file()
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
    env = _env_file()
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
    env = _env_file()
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
    env = _env_file()
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
    assert system.count("loaded") == 0 and not (config.home_dir() / ".env").exists()
    assert "not a token" not in capsys.readouterr().err


def test_install_without_accessibility_requests_it_without_waiting(system, capsys):
    system.trusted = False

    def unexpected_input(prompt=""):
        pytest.fail("installation waited for the Accessibility grant")

    assert run(input_fn=unexpected_input) == 0
    assert system.count("loaded") == len(launchd.JOBS) and launchd.plist_path("check").exists()
    captured = capsys.readouterr()
    assert t("install.done") in captured.out and captured.err == ""


@pytest.mark.parametrize("trusted,unreadable,key,calls", [
    (True, False, "install.ax.ok", (0, 0, 0)),
    ("stale", False, "install.ax.missing", (1, 1, 1)),  # reset, re-list this build, open the pane
    (False, False, "install.ax.missing", (1, 1, 1)),
    (True, True, "install.ax.unknown", (0, 1, 0))])  # unknown: only ask; a good grant is never reset
def test_install_repairs_a_grant_that_does_not_match_the_current_runner(system, capsys, trusted, unreadable,
                                                                       key, calls):
    system.trusted, system.tcc_unreadable = trusted, unreadable
    assert run() == 0  # a missing grant never fails the install
    assert (system.count("tccutil"), system.count("open"), system.count("open-pane")) == calls
    out = capsys.readouterr().out
    assert t(key, path=install.Paths().app) in out and t("install.done") in out


def test_install_skips_the_accessibility_check_when_nothing_presses(system, capsys):
    assert run(Settings(auto_play_pause=False, overtime_prompt=False)) == 0
    assert t("install.step.accessibility") not in capsys.readouterr().out


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


def test_install_builds_the_bundle_and_runner_outside_any_checkout(system):
    assert run() == 0
    p = install.Paths()
    info = plistlib.loads(p.info_plist.read_bytes())
    assert info["CFBundleIdentifier"] == "com.dannylin.monitask-center"  # the TCC identity
    runner = p.runner_sh.read_text()
    assert os.access(p.runner_sh, os.X_OK)
    assert f"{p.pylibs}:{p.src}" in runner  # pylibs first: its deps match the runner
    # launchd imports its own copy, never the checkout/venv in place (a checkout under Documents is unreadable)
    assert (p.src / "monitask_center/engine.py").read_bytes() == (install.PACKAGE / "engine.py").read_bytes()
    assert str(install.PACKAGE) not in runner
    assert f"{p.python_runner} -m monitask_center.engine" in runner and str(p.env) in runner
    assert plistlib.loads(launchd.plist_path("check").read_bytes())["ProgramArguments"][1] == str(p.runner_sh)


def _legacy_checkout(tmp_path: Path) -> Path:
    """What 1.5.0 leaves: jobs running <checkout>/run.sh, the signed bundle and pylibs in the checkout."""
    root = tmp_path / "checkout"
    runner = root / "MonitaskCenter.app/Contents/MacOS/python-runner"
    runner.parent.mkdir(parents=True)
    runner.write_text(PYTHON_VERSION)
    (root / "MonitaskCenter.app/Contents/Info.plist").write_bytes(install.INFO_PLIST)
    (root / "pylibs/AppKit").mkdir(parents=True)
    (root / ".env").write_text("TG_CHAT_ID=-1\n")
    launchd.agents_dir().mkdir(parents=True)
    for job in launchd.JOBS:
        spec = plistlib.loads(launchd.render(job, Settings()))
        spec["ProgramArguments"][1] = str(root / "run.sh")
        launchd.plist_path(job).write_bytes(plistlib.dumps(spec))
    return root


def test_migrate_moves_jobs_off_the_checkout_once(system, tmp_path):
    assert install.migrate(Settings()) is None  # nothing installed: nothing to move
    _legacy_checkout(tmp_path)
    system.deps = True  # the carried-over pylibs import fine
    assert install.migrate(Settings()) == t("install.migrated", path=tmp_path)
    p = install.Paths()
    assert p.python_runner.read_text() == PYTHON_VERSION and p.pylibs.is_dir()
    assert system.count("codesign") == 0 and system.count("pip") == 0  # same bundle: the grant survives
    assert stat.S_IMODE(p.env.stat().st_mode) == 0o600 and "TG_CHAT_ID=-1" in p.env.read_text()
    assert launchd.foreign_jobs() == [] and system.count("loaded") == len(launchd.JOBS)
    assert install.migrate(Settings()) is None and system.count("loaded") == len(launchd.JOBS)


def test_migrate_rebuilds_a_bundle_git_already_deleted(system, tmp_path):
    legacy = _legacy_checkout(tmp_path)
    shutil.rmtree(legacy / "MonitaskCenter.app")  # `git pull` of this release removes the tracked bundle
    system.deps = True
    assert install.migrate(Settings()) == t("install.migrated", path=tmp_path)
    assert system.count("codesign") == 1 and install.Paths().python_runner.read_text() == PYTHON_VERSION


def test_migrate_failure_is_a_message(system, tmp_path, monkeypatch):
    _legacy_checkout(tmp_path)
    monkeypatch.setattr(install, "_find_brew", lambda: (_ for _ in ()).throw(install.InstallError("no brew")))
    assert install.migrate(Settings()) == t("install.err.migrate", error="no brew")
    assert launchd.foreign_jobs() == list(launchd.JOBS)  # jobs left on the old, still-working runner



def test_update_from_1_5_0_syncs_onto_the_new_runtime(system, tmp_path):
    """1.5.0's "Update now" ends with launchd.sync() in the new code, before any migrate ran."""
    _legacy_checkout(tmp_path)
    config.RUNNER.unlink()  # conftest's placeholder: nothing built the runtime yet
    system.deps = True
    assert launchd.sync(Settings()) == ("reinstalled", list(launchd.JOBS))
    assert launchd.foreign_jobs() == [] and config.RUNNER.is_file()
    assert system.count("codesign") == 0  # the checkout's signed bundle was carried over


def _package(tmp_path: Path, monkeypatch) -> Path:
    pkg = tmp_path / "checkout/src/monitask_center"
    (pkg / "locales").mkdir(parents=True)
    (pkg / "engine.py").write_text("v1\n")
    (pkg / "locales/core.json").write_text("{}\n")
    (pkg / "__pycache__").mkdir()
    (pkg / "__pycache__/engine.pyc").write_text("cache")
    monkeypatch.setattr(install, "PACKAGE", pkg)
    return pkg


def test_sync_package_copies_the_package_once_and_follows_edits(tmp_path, monkeypatch):
    pkg = _package(tmp_path, monkeypatch)
    p = install.Paths()
    copy = p.src / "monitask_center"
    assert install.sync_package(p) is True
    assert (copy / "engine.py").read_text() == "v1\n" and (copy / "locales/core.json").is_file()
    assert not (copy / "__pycache__").exists()
    (copy / "__pycache__").mkdir()  # what the launchd python writes next to the copy
    assert install.sync_package(p) is False
    (pkg / "engine.py").write_text("v2, edited in the checkout\n")
    (pkg / "locales/core.json").unlink()
    assert install.sync_package(p) is True
    assert (copy / "engine.py").read_text() == "v2, edited in the checkout\n"
    assert not (copy / "locales/core.json").exists()
    assert sorted(x.name for x in p.src.iterdir()) == ["monitask_center"]  # no staging leftovers


@pytest.mark.parametrize("where", ["_activity", "../../activity"])  # the wheel's copy, an editable checkout's
def test_migrate_brings_a_stale_activity_copy_to_this_release_once(tmp_path, monkeypatch, where):
    """1.9.5 and older never refreshed the activity job's copy on upgrade: it lacked `run.sh record`."""
    source = _package(tmp_path, monkeypatch) / where
    source.mkdir(parents=True)
    for name in install.ACTIVITY_FILES:
        (source / name).write_text(f"new {name}\n")
    (source / "config.json").write_text("{}\n")
    act = launchd.activity_runner().parent
    (act / "profile").mkdir(parents=True)  # the login session
    (act / "run.sh").write_text("usage: run.sh {login | fetch [force] | now}\n")
    (act / "config.json").write_text('{"memberId": "user_test_001"}\n')  # may be edited by hand: kept
    launchd.agents_dir().mkdir(parents=True)
    launchd.plist_path("activity").write_bytes(plistlib.dumps({
        "ProgramArguments": ["/bin/bash", str(launchd.activity_runner()), "fetch"],
        "StartCalendarInterval": launchd.ACTIVITY_TICKS}))
    assert install.migrate(Settings()) == t("install.activity.synced", path=act)
    assert all((act / name).read_text() == f"new {name}\n" for name in install.ACTIVITY_FILES)
    assert (act / "src/monitask_center/engine.py").read_text() == "v1\n"  # the package it reads settings with
    assert (act / "profile").is_dir() and "user_test_001" in (act / "config.json").read_text()
    assert install.migrate(Settings()) is None


def test_migrate_leaves_activity_alone_without_the_job(tmp_path, monkeypatch):
    (_package(tmp_path, monkeypatch) / "_activity").mkdir()
    assert install.migrate(Settings()) is None and not launchd.activity_runner().parent.exists()


def test_migrate_moves_a_runner_off_the_package_in_place_once(system, tmp_path, monkeypatch):
    """1.9.1 and older: run.sh imported the checkout's (or the venv's) package in place."""
    _package(tmp_path, monkeypatch)
    assert run() == 0
    p = install.Paths()
    p.pylibs.mkdir(exist_ok=True)  # the fake pip creates nothing; a built runtime has it
    old = p.runner_sh.read_text().replace(str(p.src), str(install.PACKAGE.parent))
    p.runner_sh.write_text(old)
    shutil.rmtree(p.src)
    assert install.migrate(Settings()) == t("install.migrated", path=p.root)
    assert f"{p.pylibs}:{p.src}" in p.runner_sh.read_text() and (p.src / "monitask_center/engine.py").is_file()
    assert install.migrate(Settings()) is None
