import io
import os
import plistlib
import sys
from datetime import datetime

import pytest

from monitask_center import (
    cli,
    config,
    i18n,
    install,
    launchd,
    status,
    update_check,
    workday,
)
from monitask_center.i18n import t
from monitask_center.tui import menu

REAL_LOAD_CALENDAR = workday.load_calendar
WORKDAY = datetime(2026, 10, 7, 9, 0)  # a Wednesday


def _calendar(monkeypatch, verdict):
    monkeypatch.setattr(workday, "peek_status", lambda d, s: verdict)


def test_install_and_uninstall_route_exit_codes(monkeypatch):
    monkeypatch.setattr(install, "run_install", lambda s: 7)
    monkeypatch.setattr(install, "uninstall", lambda: 3)
    assert cli.main(["install"]) == 7
    assert cli.main(["uninstall"]) == 3


@pytest.mark.parametrize("argv", [["--config"], ["config"]])
def test_config_flag_and_bare_word(monkeypatch, argv):
    seen = {}

    def fake_menu(stdin=None, stdout=None, status_lines=None):
        seen["lines"] = status_lines
        return 5

    monkeypatch.setattr(menu, "config_menu", fake_menu)
    monkeypatch.setattr(status, "runner_status", status.runner_status)  # restored after the cache wrap
    monkeypatch.setattr(status, "status_lines", lambda s: ["line"])
    assert cli.main(argv) == 5
    assert seen["lines"]() == ["line"]


def test_status_prints_snapshot_rows(monkeypatch, capsys):
    monkeypatch.setattr(status, "snapshot", lambda s: [status.Row("workday", "L1", "V1", "ok")])
    assert cli.main(["status"]) == 0
    assert capsys.readouterr().out == "L1: V1\n"


@pytest.mark.parametrize("cmd", ["check", "dump-windows"])
def test_engine_commands_exec_run_sh(monkeypatch, cmd):
    calls = []
    monkeypatch.setattr(os, "execv", lambda path, args: calls.append((path, args)))
    assert cli.main([cmd]) == 1  # patched exec "returns", as a real failed exec would
    assert calls == [("/bin/bash", ["/bin/bash", str(config.RUNNER), cmd])]


def test_logs_path_and_tail(monkeypatch, capsys):
    assert cli.main(["logs", "--path"]) == 0
    assert capsys.readouterr().out.strip() == str(config.data_dir() / "monitask.log")
    assert cli.main(["logs"]) == 1  # no log file yet
    path = cli.log_path()
    path.parent.mkdir(parents=True)
    path.write_text("x\n")
    calls = []
    monkeypatch.setattr(os, "execv", lambda p, a: calls.append((p, a)))
    cli.main(["logs"])
    assert calls == [("/usr/bin/tail", ["tail", "-n", "50", "-f", str(path)])]


def test_no_args_prints_help(capsys):
    assert cli.main([]) == 0
    assert "--config" in capsys.readouterr().out


def test_help_lists_every_command(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for word in ("--config", *cli.COMMANDS):
        assert word in out
    assert t("cli.epilog") in " ".join(out.split())  # argparse re-wraps the epilog
    assert t("cli.description") in out


def test_abbreviations_rejected(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--conf"])
    assert exc.value.code == 2


def test_help_follows_configured_language(monkeypatch, capsys):
    monkeypatch.setattr(i18n, "available", lambda: ["zh-TW", "xx"])
    monkeypatch.setattr(i18n, "catalog", lambda lang: {"cli.description": "XX DESC"} if lang == "xx" else
                        {"cli.description": "zh"})
    monkeypatch.setattr(config, "load", lambda: config.Settings(language="xx"))
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert "XX DESC" in capsys.readouterr().out


def test_config_non_tty_has_no_escape_codes(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr(status, "runner_status", lambda: None)
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    monkeypatch.setattr(sys, "stdout", out)
    assert cli.main(["--config"]) == 0
    assert out.getvalue()
    assert "\x1b" not in out.getvalue()


def test_plan_marks_lunch_and_next(monkeypatch, capsys):
    _calendar(monkeypatch, (True, "workday"))
    monkeypatch.setattr(status, "_next_check", lambda now, s: datetime(2026, 10, 7, 10, 0))
    assert cli.cmd_plan(WORKDAY) == 0
    out = capsys.readouterr().out
    assert t("status.hours.lunch_mark", hour=12) in out
    assert t("status.workday.yes") in out
    runs = next(line for line in out.splitlines() if line.startswith(t("cli.plan.runs")))
    assert "12:00" not in runs and "13:00" in runs and "10:00" in runs
    assert "2026-10-07 10:00" in out


def test_plan_on_vacation_day_runs_nothing(monkeypatch, capsys):
    monkeypatch.setattr(workday, "peek_status",
                        lambda d, s: (False, "vacation") if d == WORKDAY.date() else (True, "workday"))
    assert cli.cmd_plan(WORKDAY) == 0
    out = capsys.readouterr().out
    assert t("status.workday.no") in out and t("status.reason.vacation") in out
    assert "vacation" not in out
    assert f"{t('cli.plan.runs')}: {t('cli.plan.none')}" in out
    assert "2026-10-08 10:00" in out  # next check skips the vacation day


def test_plan_never_notifies(monkeypatch, capsys):
    sent = []
    monkeypatch.setattr(workday, "load_calendar", REAL_LOAD_CALENDAR)
    monkeypatch.setattr(workday, "_fetch", lambda year: (None, "boom"))  # download failed
    monkeypatch.setattr(workday, "notify", lambda *a, **k: sent.append(a))
    assert cli.cmd_plan(WORKDAY) == 0
    out = capsys.readouterr().out
    assert sent == [] and t("status.reason.calendar-unavailable(fail-open)") in out
    assert "fail-open" not in out


def test_help_is_localized_without_argparse_english(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    assert out.startswith(t("cli.usage_prefix")) and t("cli.help.help") in out
    assert t("cli.section.options") in out and t("cli.section.positional") in out
    for english in ("usage:", "options:", "positional arguments", "show this help"):
        assert english not in out
    with pytest.raises(SystemExit):
        cli.main(["logs", "-h"])
    assert "show this help" not in capsys.readouterr().out


def test_parse_error_prefix_is_localized(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--conf"])
    err = capsys.readouterr().err
    assert exc.value.code == 2 and t("cli.error", prog="monitask-center", message="") .strip() in err
    assert "error:" not in err and err.startswith(t("cli.usage_prefix"))


def test_update_installs_the_release_tag_then_migrates_with_the_new_code():
    steps = cli._upgrade(update_check.UpdateAvailable(current="1.5.0", latest="v1.6.0"))
    assert steps[0] == ["uv", "tool", "install", "--force", "--from",
                        "git+https://github.com/weskao/monitask-center.git@v1.6.0", "monitask-center"]
    assert steps[1][0] == sys.executable and "install.migrate" in steps[1][-1] and "launchd.sync" in steps[1][-1]


def test_update_migrate_message_ends_its_line(monkeypatch, capsys):
    # The next line ("🎉 ...updated") must not be glued onto the migration notice.
    monkeypatch.setattr(config, "load", lambda: None)
    monkeypatch.setattr(install, "migrate", lambda s: "moved")
    monkeypatch.setattr(launchd, "sync", lambda s: None)
    exec(cli._MIGRATE, {})
    assert capsys.readouterr().out == "moved\n"
    monkeypatch.setattr(install, "migrate", lambda s: None)
    exec(cli._MIGRATE, {})
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("argv", [["activity"], ["--activity"]])
def test_activity_execs_the_installed_run_sh_now(monkeypatch, tmp_path, argv):
    script = tmp_path / "run.sh"
    script.write_text("")
    plist = tmp_path / "activity.plist"
    plist.write_bytes(plistlib.dumps({"ProgramArguments": ["/bin/bash", str(script), "fetch"]}))
    monkeypatch.setattr(launchd, "plist_path", lambda job: plist)
    calls = []
    monkeypatch.setattr(os, "execv", lambda path, args: calls.append((path, args)))
    assert cli.main(argv) == 1  # patched exec "returns"
    assert calls == [("/bin/bash", ["/bin/bash", str(script), "now"])]


def test_activity_without_the_job_points_to_its_installer(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(launchd, "plist_path", lambda job: tmp_path / "missing.plist")
    monkeypatch.setattr(os, "execv", lambda *a: pytest.fail("must not exec"))
    assert cli.main(["activity"]) == 1
    assert t("cli.activity.missing") in capsys.readouterr().err


@pytest.mark.parametrize("cmd", cli.COMMANDS)
def test_every_command_also_answers_to_its_dashed_form(cmd):
    assert cli._normalize_argv([f"--{cmd}", "x"]) == [cmd, "x"]
    assert cli._normalize_argv([cmd, "x"]) == [cmd, "x"]


def test_commands_migrate_the_activity_schedule(monkeypatch, capsys):
    monkeypatch.setattr(launchd, "migrate_activity", lambda: "activity moved")
    monkeypatch.setattr(status, "snapshot", lambda s: [])
    assert cli.main(["status"]) == 0
    assert "activity moved" in capsys.readouterr().err
