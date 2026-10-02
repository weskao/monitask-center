import shutil
import subprocess

import pytest

from monitask_center import config, notify

NASTY = ["it's", 'say "hi"', "back\\slash", "line1\nline2", "\u4e2d\u6587 'mixed' \"q\" \\", "-e rm -rf", "\" & (do shell script \"x\")"]


@pytest.mark.parametrize("message", NASTY)
def test_command_keeps_text_as_argv(message):
    cmd = notify.osascript_command(message, "T'itle")
    assert cmd[0] == "/usr/bin/osascript"
    assert cmd[-3:] == ["--", message, "T'itle"]  # data, after the option terminator
    scripts = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-e"]
    assert scripts == list(notify.OSASCRIPT_LINES)
    assert all(message not in s and "T'itle" not in s for s in scripts)  # never spliced into code


def test_script_displays_argv_items_in_order():
    display = " ".join(notify.OSASCRIPT_LINES)
    assert "item 1 of argv" in notify.OSASCRIPT_LINES[1] and "item 2 of argv" in notify.OSASCRIPT_LINES[1]
    assert display.index("item 1 of argv") < display.index("item 2 of argv")
    cmd = notify.osascript_command("MSG", "TTL")
    assert cmd[cmd.index("--") + 1:] == ["MSG", "TTL"]  # message is item 1, title item 2


@pytest.mark.skipif(shutil.which("osacompile") is None, reason="osacompile not available")
def test_script_compiles(tmp_path):
    cmd = ["osacompile", "-o", str(tmp_path / "n.scpt")]
    for line in notify.OSASCRIPT_LINES:
        cmd += ["-e", line]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr


def _capture(monkeypatch):
    logged = []
    monkeypatch.setattr(notify, "log", logged.append)
    return logged


def test_macos_failure_is_logged_not_raised(monkeypatch):
    logged = _capture(monkeypatch)
    monkeypatch.setattr(notify.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "boom: syntax"))
    notify.macos_notification("hi")
    assert len(logged) == 1 and "boom: syntax" in logged[0] and "1" in logged[0]
    assert "notify." not in logged[0]  # catalog key resolved


def test_macos_oserror_and_timeout_are_logged(monkeypatch):
    logged = _capture(monkeypatch)
    for exc in (FileNotFoundError("no osascript"), subprocess.TimeoutExpired("osascript", 10)):
        def boom(*a, _e=exc, **k):
            raise _e

        monkeypatch.setattr(notify.subprocess, "run", boom)
        notify.macos_notification("hi")
    assert len(logged) == 2 and "notify." not in logged[0]


def test_macos_success_logs_nothing(monkeypatch):
    logged = _capture(monkeypatch)
    monkeypatch.setattr(notify.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    notify.macos_notification("hi")
    assert logged == []


@pytest.fixture
def macos(monkeypatch):
    calls = []
    monkeypatch.setattr(notify, "macos_notification", lambda m, *a: calls.append(m))
    monkeypatch.setattr(notify, "host_name", lambda: "host")
    return calls


def test_telegram_off_uses_macos(monkeypatch, macos):
    monkeypatch.setattr(notify, "telegram", lambda m: pytest.fail("telegram must not be called"))
    notify.notify("m", use_telegram=False)
    assert macos == ["m"]


def test_missing_tg_send_falls_back(monkeypatch, tmp_path, macos):
    monkeypatch.setattr(notify, "TG_SEND", tmp_path / "absent.sh")
    notify.notify("m")
    assert macos == ["m"]


def test_send_failure_falls_back_and_success_does_not(monkeypatch, tmp_path, macos):
    script = tmp_path / "tg-send.sh"
    script.write_text("#!/bin/bash\n")
    monkeypatch.setattr(notify, "TG_SEND", script)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    rc = {"send": 1}
    monkeypatch.setattr(notify, "run", lambda cmd, **k: subprocess.CompletedProcess(
        cmd, rc["send"] if "send" in cmd else 0))
    notify.notify("m")
    assert macos == ["m"]
    rc["send"] = 0
    notify.notify("m2")
    assert macos == ["m"]  # delivered via telegram, no fallback


def test_telegram_exception_is_logged_and_falls_back(monkeypatch, macos):
    logged = _capture(monkeypatch)

    def boom(m):
        raise RuntimeError("kaput")

    monkeypatch.setattr(notify, "telegram", boom)
    notify.notify("m")
    assert macos == ["m"] and "kaput" in logged[0] and "notify." not in logged[0]


def test_log_is_file_only_unless_env(monkeypatch, capsys):
    monkeypatch.delenv("MONITASK_CENTER_LOG_STDOUT", raising=False)
    notify.log("quiet")
    assert capsys.readouterr().out == ""
    assert "quiet" in (config.data_dir() / "monitask.log").read_text(encoding="utf-8")
    monkeypatch.setenv("MONITASK_CENTER_LOG_STDOUT", "1")
    notify.log("loud")
    assert "loud" in capsys.readouterr().out
