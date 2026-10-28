import plistlib
import subprocess
import sys

import pytest

from monitask_center import config, launchd
from monitask_center.config import Settings
from monitask_center.i18n import t


@pytest.fixture
def calls(monkeypatch):
    log = []

    def fake(*args, check=False):
        log.append(args)

    monkeypatch.setattr(launchd, "_launchctl", fake)
    return log


def test_job_plists():
    assert launchd.JOBS == ("check", "overtime")
    spec = plistlib.loads(launchd.render("check", Settings()))
    assert spec["Label"] == "com.monitask-center.check"
    assert spec["ProgramArguments"][-1] == "check"
    assert [i["Hour"] for i in spec["StartCalendarInterval"]] == list(range(10, 19))
    assert all(i["Minute"] == 0 for i in spec["StartCalendarInterval"])
    assert "KeepAlive" not in spec
    overtime = plistlib.loads(launchd.render("overtime", Settings()))
    assert overtime["ProgramArguments"][-1] == "overtime"
    assert overtime["StartCalendarInterval"] == [{"Hour": 18, "Minute": 30}]
    assert plistlib.loads(launchd.render("overtime", Settings(overtime_minutes=20 * 60)))[
        "StartCalendarInterval"] == [{"Hour": 20, "Minute": 0}]


def test_lunch_settings_do_not_change_plist():
    base = launchd.render("check", Settings())
    for s in (Settings(lunch_skip=False), Settings(lunch_start_hour=11, lunch_end_hour=15),
              Settings(telegram=False, vacation=("2026-10-05",), language="zh-TW")):
        assert launchd.render("check", s) == base


def test_sync_not_installed(calls):
    assert launchd.sync(Settings()) == ("not_installed", [])
    assert calls == [] and not launchd.plist_path("check").exists()
    assert launchd.sync_message("not_installed", []) != "launchd.sync.not_installed"


def test_sync_lunch_change_no_drift_hour_change_drifts_only_check(calls):
    launchd.install(Settings())
    calls.clear()
    assert launchd.sync(Settings(lunch_start_hour=11, lunch_end_hour=14, lunch_skip=False)) == ("unchanged", [])
    assert calls == []
    assert launchd.stale_jobs(Settings(start_hour=9)) == ["check"]
    assert launchd.sync(Settings(start_hour=9)) == ("reinstalled", ["check"])
    hours = [i["Hour"] for i in plistlib.loads(launchd.plist_path("check").read_bytes())["StartCalendarInterval"]]
    assert hours == list(range(9, 19))
    assert [c[0] for c in calls] == ["bootout", "bootstrap"]
    assert "reload" not in launchd.sync_message("reinstalled", ["check"])  # catalog resolved, not key


def test_install_missing_adds_only_jobs_without_a_plist(calls):
    assert launchd.install_missing(Settings()) is None  # nothing installed: not ours to install
    launchd.install(Settings(), ["check"])  # what "Update now" from 1.4.0 leaves behind
    calls.clear()
    note = launchd.install_missing(Settings(start_hour=9))  # check is stale too, but must stay loaded
    assert "overtime" in note and launchd.plist_path("overtime").exists()
    assert {c[-1] for c in calls} == {str(launchd.plist_path("overtime"))}
    calls.clear()
    assert launchd.install_missing(Settings()) is None and calls == []


def test_install_missing_failure_is_a_message(calls, monkeypatch):
    launchd.install(Settings(), ["check"])

    def fail(*_a, **_k):
        raise launchd.LaunchdError(5, ["launchctl"], "Bootstrap failed")

    monkeypatch.setattr(launchd, "install", fail)
    assert "Bootstrap failed" in launchd.install_missing(Settings())


def test_sync_drifted_reloads_only_stale_jobs(calls):
    assert launchd.sync_drifted(Settings()) is None  # nothing installed: not ours to install
    launchd.install(Settings(start_hour=6))  # a schedule change that never reached launchd
    calls.clear()
    note = launchd.sync_drifted(Settings())
    assert "check" in note and "overtime" not in note
    assert launchd.stale_jobs(Settings()) == []
    assert {c[-1] for c in calls} == {str(launchd.plist_path("check"))}
    calls.clear()
    assert launchd.sync_drifted(Settings()) is None and calls == []


def test_sync_drifted_failure_is_a_message(calls, monkeypatch):
    launchd.install(Settings(start_hour=6))

    def fail(*_a, **_k):
        raise launchd.LaunchdError(5, ["launchctl"], "Bootstrap failed")

    monkeypatch.setattr(launchd, "install", fail)
    assert "Bootstrap failed" in launchd.sync_drifted(Settings())


def test_install_removes_legacy_plists(calls):
    launchd.agents_dir().mkdir(parents=True)
    legacy = [launchd.plist_path(j, p)
              for p in launchd.LEGACY_PREFIXES for j in ("morning", "watch", "night-stop")]
    legacy.append(launchd.plist_path("check", "com.dannylin.monitask-center"))
    keep = launchd.agents_dir() / "com.other.thing.plist"
    for p in (*legacy, keep):
        p.write_bytes(b"x")
    launchd.install(Settings())
    assert not any(p.exists() for p in legacy)
    assert keep.exists() and launchd.plist_path("check").exists()
    assert sum(c[0] == "bootout" for c in calls) >= len(legacy)


def test_uninstall_removes_job_and_legacy(calls):
    launchd.install(Settings())
    legacy = launchd.plist_path("watch")
    legacy.write_bytes(b"x")
    assert launchd.uninstall() == ["check", "overtime"]
    assert not legacy.exists() and not launchd.plist_path("check").exists()
    assert not launchd.is_installed()


def test_bootstrap_failure_raises(monkeypatch):
    def fake(*args, check=False):
        if check:
            raise subprocess.CalledProcessError(5, ["launchctl", *args])

    monkeypatch.setattr(launchd, "_launchctl", fake)
    with pytest.raises(subprocess.CalledProcessError):
        launchd.install(Settings())
    assert not launchd.is_installed()  # rolled back: nothing matches the new settings while unloaded
    assert launchd.stale_jobs(Settings()) == ["check", "overtime"]
    assert launchd.sync(Settings()) == ("not_installed", [])


def test_bootstrap_failure_restores_previous_plist(monkeypatch, calls):
    launchd.install(Settings())
    old = launchd.plist_path("check").read_bytes()

    def fail(*args, check=False):
        if check:
            raise subprocess.CalledProcessError(5, ["launchctl", *args])

    monkeypatch.setattr(launchd, "_launchctl", fail)
    with pytest.raises(subprocess.CalledProcessError):
        launchd.sync(Settings(start_hour=9))
    assert launchd.plist_path("check").read_bytes() == old
    assert launchd.stale_jobs(Settings(start_hour=9)) == ["check"]  # a later sync retries
    assert launchd.sync(Settings()) == ("unchanged", [])


def _list_output(monkeypatch, out):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, out, ""))


def test_loaded_matches_label_column_exactly(monkeypatch):
    _list_output(monkeypatch, "PID\tStatus\tLabel\n"
                 "-\t0\tcom.monitask-center.activity\n"
                 "123\t0\tcom.monitask-center.check\n"
                 "-\t0\tcom.monitask-center.check.extra\n"
                 "-\t0\tcom.apple.com.monitask-center.check\n"
                 "-\t0\tcom.other.com.monitask-center\n")
    assert launchd.loaded() == ["123\t0\tcom.monitask-center.check"]


def test_loaded_ignores_activity_job_and_header(monkeypatch):
    _list_output(monkeypatch, "PID\tStatus\tLabel\n-\t0\tcom.monitask-center.activity\n")
    assert launchd.loaded() == []
    _list_output(monkeypatch, "")
    assert launchd.loaded() == []


def test_install_builds_a_missing_runner_or_fails_as_os_error(calls, monkeypatch, tmp_path):
    from monitask_center import install

    monkeypatch.setattr(config, "RUNNER", tmp_path / "missing.sh")
    built = []
    monkeypatch.setattr(install, "ensure_runtime", lambda: built.append(1) or config.RUNNER.write_text(""))
    assert launchd.install(Settings(), ["check"]) == ["check"] and built == [1]  # 1.5.0's sync step
    config.RUNNER.unlink()

    def fail():
        raise install.InstallError("no brew")

    monkeypatch.setattr(install, "ensure_runtime", fail)
    with pytest.raises(FileNotFoundError):  # an OSError: every caller already reports those
        launchd.install(Settings())


def test_checked_launchctl_failure_is_captured_not_printed(monkeypatch, capfd):
    """A child that writes to stderr and exits 5 stands in for launchctl; its text must not
    reach the terminal, and the error keeps the code plus the first stderr line only."""
    real_run = subprocess.run
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw)
        script = "import sys;sys.stderr.write('Bootstrap failed: 5: Input/output error\\nsecond line\\n');sys.exit(5)"
        return real_run([sys.executable, "-c", script], **kw)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(launchd.LaunchdError) as info:
        launchd._launchctl("bootstrap", "gui/501", "/x.plist", check=True)
    err = info.value
    assert isinstance(err, subprocess.CalledProcessError)
    assert err.returncode == 5 and err.detail == "Bootstrap failed: 5: Input/output error"
    assert seen["stderr"] == subprocess.PIPE
    text = str(err)
    assert text == launchd.describe_error(err) and "5" in text and err.detail in text
    assert "second line" not in text and "launchctl, " not in text and "[" not in text
    assert capfd.readouterr() == ("", "")


def test_unchecked_launchctl_failure_is_silent(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 3, "", "boom"))
    launchd._launchctl("bootout", "gui/501", "/x.plist")  # no raise


def test_describe_error_variants():
    assert "7" in launchd.describe_error(subprocess.CalledProcessError(7, ["launchctl", "x"]))
    assert "launchctl" in launchd.describe_error(subprocess.CalledProcessError(7, ["launchctl", "x"]))
    assert "["  not in launchd.describe_error(subprocess.CalledProcessError(7, ["launchctl", "x"]))
    assert launchd.describe_error(OSError("synthetic")) == "synthetic"
    long = launchd._first_line("\x1b[31m" + "x" * 200)
    assert len(long) <= launchd.DETAIL_MAX and "\x1b" not in long


def _assert_not_inherited(kw):
    ok = (subprocess.DEVNULL, subprocess.PIPE)
    captured = kw.get("capture_output") is True
    # None / missing = inherited: would smear the TUI frame
    assert captured or kw.get("stderr") in ok, kw
    assert captured or kw.get("stdout") in ok, kw


@pytest.fixture
def runs(monkeypatch):
    """Record every subprocess.run call; `fail_bootstrap` makes checked bootstraps exit 5."""
    class Log(list):
        state = {"fail_bootstrap": False}

    log, state = Log(), Log.state

    def fake(cmd, **kw):
        log.append((cmd, kw))
        rc = 5 if state["fail_bootstrap"] and cmd[1] == "bootstrap" and kw.get("stderr") == subprocess.PIPE else 0
        return subprocess.CompletedProcess(cmd, rc, "", "boom" if rc else "")

    monkeypatch.setattr(subprocess, "run", fake)
    return log


def test_install_and_legacy_cleanup_never_inherit_stdio(runs):
    launchd.agents_dir().mkdir(parents=True)
    launchd.plist_path("watch").write_bytes(b"x")  # forces a legacy bootout
    launchd.install(Settings())
    verbs = [c[1] for c, _ in runs]
    assert verbs.count("bootout") >= 2 and "bootstrap" in verbs
    for _, kw in runs:
        _assert_not_inherited(kw)


def test_uninstall_never_inherits_stdio(runs):
    launchd.install(Settings())
    launchd.plist_path("watch").write_bytes(b"x")
    runs.clear()
    launchd.uninstall()
    assert [c[1] for c, _ in runs].count("bootout") >= 2  # job + legacy
    for _, kw in runs:
        _assert_not_inherited(kw)


def test_rollback_rebootstrap_never_inherits_stdio(runs):
    launchd.install(Settings())
    runs.clear()
    runs.state["fail_bootstrap"] = True
    with pytest.raises(launchd.LaunchdError):
        launchd.install(Settings(start_hour=9))
    boots = [kw for c, kw in runs if c[1] == "bootstrap"]
    assert len(boots) == 2  # the checked attempt, then the best-effort restore of the old job
    assert boots[0]["stderr"] == subprocess.PIPE and boots[1]["stderr"] == subprocess.DEVNULL
    for _, kw in runs:
        _assert_not_inherited(kw)


def test_loaded_never_inherits_stdio(runs):
    launchd.loaded()
    assert len(runs) == 1
    _assert_not_inherited(runs[0][1])


def test_unchecked_launchctl_child_stderr_never_reaches_terminal(monkeypatch, capfd):
    real_run = subprocess.run
    script = "import sys;sys.stderr.write('Boot-out failed: 3: No such process\\n');sys.exit(3)"
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: real_run([sys.executable, "-c", script], **kw))
    launchd._launchctl("bootout", "gui/501", "/x.plist")
    assert capfd.readouterr() == ("", "")


def _activity_plist(ticks, script=None):
    path = launchd.plist_path("activity")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps({
        "Label": "com.monitask-center.activity",
        "ProgramArguments": ["/bin/bash", str(script or launchd.activity_runner()), "fetch"],
        "EnvironmentVariables": {"MONITASK_ACTIVITY_NODE": "/opt/node"},
        "StartCalendarInterval": ticks,
    }))
    return path


def test_activity_ticks_cover_every_alert_frequency():
    minutes = [tick["Minute"] for tick in launchd.ACTIVITY_TICKS]
    assert minutes == list(range(0, 60, 10))
    assert all(f % 10 == 0 for f in config.ALERT_MINUTES)  # checks counted from the window start land on ticks


@pytest.mark.parametrize("old_ticks", [
    [{"Minute": 0}],                             # what activity/install.sh wrote before 1.10
    [{"Minute": m} for m in range(0, 60, 5)],    # the unreleased 5-minute schedule
])
def test_migrate_activity_moves_the_job_to_ten_minute_ticks_once(calls, old_ticks):
    assert launchd.migrate_activity() is None and calls == []  # no activity job: not ours to add
    path = _activity_plist(old_ticks)
    assert launchd.migrate_activity() == t("launchd.activity.migrated")
    spec = plistlib.loads(path.read_bytes())
    assert spec["StartCalendarInterval"] == launchd.ACTIVITY_TICKS
    assert spec["ProgramArguments"][1] == str(launchd.activity_runner())
    assert spec["EnvironmentVariables"] == {"MONITASK_ACTIVITY_NODE": "/opt/node"}
    assert [c[0] for c in calls] == ["bootout", "bootstrap"]
    calls.clear()
    assert launchd.migrate_activity() is None and calls == []


def test_migrate_activity_failure_is_a_message(monkeypatch):
    _activity_plist([{"Minute": 0}])

    def fail(*args, check=False):
        if check:
            raise launchd.LaunchdError(5, ["launchctl"], "Bootstrap failed")

    monkeypatch.setattr(launchd, "_launchctl", fail)
    assert "Bootstrap failed" in launchd.migrate_activity()


def _checkout_installer(tmp_path, body):
    installer = tmp_path / "checkout/activity/install.sh"
    installer.parent.mkdir(parents=True)
    installer.write_text(body)
    return installer


def test_migrate_activity_moves_a_checkout_job_onto_the_copy_once(tmp_path, calls):
    # Pre-upgrade state: the job runs the checkout (unreadable to launchd under Documents).
    installer = _checkout_installer(tmp_path, f'''#!/bin/bash
echo "$* $MONITASK_ACTIVITY_NODE" >> "{tmp_path}/sync.log"
python3 - <<'PY'
import plistlib, pathlib
p = pathlib.Path("{launchd.plist_path("activity")}")
spec = plistlib.loads(p.read_bytes())
spec["ProgramArguments"][1] = "{launchd.activity_runner()}"
p.write_bytes(plistlib.dumps(spec))
PY
''')
    _activity_plist([{"Minute": 0}], installer.parent / "run.sh")
    assert launchd.migrate_activity() == t("launchd.activity.moved", path=launchd.activity_runner().parent)
    assert (tmp_path / "sync.log").read_text() == "sync /opt/node\n"  # the job's own Node, non-interactive
    assert launchd.runner("activity") == str(launchd.activity_runner())
    # Second run: already on the copy and on ACTIVITY_TICKS would be next; nothing to move again.
    path = launchd.plist_path("activity")
    spec = plistlib.loads(path.read_bytes())
    path.write_bytes(plistlib.dumps({**spec, "StartCalendarInterval": launchd.ACTIVITY_TICKS}))
    assert launchd.migrate_activity() is None and calls == []
    assert (tmp_path / "sync.log").read_text() == "sync /opt/node\n"


@pytest.mark.parametrize("body,key,error", [
    # the hourly check under launchd: macOS privacy protection refuses the checkout
    ("#!/bin/bash\necho 'install.sh: Operation not permitted' >&2; exit 126\n", "launchd.err.activity_move",
     t("launchd.err.installer", code=126, detail="install.sh: Operation not permitted")),
    # a checkout older than the sync command: only a git pull helps
    ("#!/bin/bash\necho 'usage: install.sh {install|uninstall|reload|status}' >&2; exit 2\n",
     "launchd.err.activity_outdated",
     t("launchd.err.installer", code=2, detail="usage: install.sh {install|uninstall|reload|status}")),
    ("#!/bin/bash\nexit 3\n", "launchd.err.activity_move", t("launchd.err.installer_code", code=3)),
    # claims success but the job still runs the checkout
    ("#!/bin/bash\nexit 0\n", "launchd.err.activity_move", t("launchd.err.installer_unmoved")),
])
def test_migrate_activity_move_failure_is_a_message(tmp_path, calls, body, key, error):
    installer = _checkout_installer(tmp_path, body)
    path = _activity_plist([{"Minute": 0}], installer.parent / "run.sh")
    before = path.read_bytes()
    assert "launchctl" not in error
    assert launchd.migrate_activity() == t(key, error=error)
    assert path.read_bytes() == before and calls == []


def test_migrate_activity_without_the_checkout_is_a_message(tmp_path, calls):
    _activity_plist([{"Minute": 0}], tmp_path / "gone/activity/run.sh")
    assert launchd.migrate_activity().startswith(t("launchd.err.activity_move", error="")[:20])


def test_migrate_activity_ignores_an_unreadable_plist(calls):
    path = launchd.plist_path("activity")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not a plist")
    assert launchd.migrate_activity() is None and calls == []
