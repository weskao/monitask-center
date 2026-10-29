"""The activity reporter must use the same hours and days as the timer checker."""

import json
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from monitask_center import config, launchd, status

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not shutil.which("node"), reason="activity requires Node")
@pytest.mark.parametrize("status,url,html,expected", [
    (200, "/Report/TimeSpanPartial", '''
      <style>.example { width: 99%; }</style><script>const example = '99%';</script>
      <div>99%</div>
      <div class="timeline__online-widget_medium-text">00H 00米</div>
      <div class="timeline__online-widget_medium-text">67.25%</div>
      <div class="timeline__online-widget_online-text">02H 15米</div>
      <a class="all-screenshots-link"><i class="icon"></i><span>42 截图</span></a>
      <div class="screen"><span class="screen_time"> 14:10 </span>
        <a title="Default 上 2026/10/07 14:12:30 - 截图 1 的 1"></a>
        <span class="ml-1">70% 的 10 分钟。</span></div>
      <div class="screen"></div>
      <div class="screen"><span class="screen_time"> 14:20 </span>
        <span class="ml-1">80% 的 5 分钟。</span></div>
    ''', {"daily": "67.25", "blocks": [70, 80],
          "latest": {"time": "14:20", "rate": 80, "minutes": 5},
          "total": "02H 15分", "shots": "42"}),
    (200, "/Report/TimeSpanPartial",
     '<div class="timeline__online-widget_medium-text">0.00%</div>',
     {"daily": "0.00", "blocks": [], "latest": None, "total": None, "shots": None}),
    (200, "/Report/TimeSpanPartial", '<div>99%</div>', "報表資料缺少有效的活動率"),
    (200, "/Report/TimeSpanPartial",
     '<div class="timeline__online-widget_medium-text">101%</div>',
     "報表資料缺少有效的活動率"),
    (200, "/identity/login", '<form>Log In</form>', "登入過期"),
    (401, "/Report/TimeSpanPartial", "", "登入過期"),
    (403, "/Report/TimeSpanPartial", "", "報表請求失敗（HTTP 403）"),
    (200, "/Report/TimeSpanPartial", "这一天没有时间被跟踪。", "當日尚無追蹤資料"),
    (503, "/Report/TimeSpanPartial", "", "報表請求失敗（HTTP 503）"),
    (0, "/Report/TimeSpanPartial", "", "報表請求失敗（網路錯誤或逾時）"),
])
def test_activity_reads_server_report_without_rendering(status, url, html, expected):
    script = """
const assert = require('node:assert/strict');
const {CONFIG, fetchActivity} = require('./activity/lib');
const [status, path, html] = JSON.parse(process.argv[1]);
const request = {get: async (url, options) => {
  const target = new URL(url);
  if (target.pathname === '/report') return {status: () => 200, ok: () => true,
    url: () => 'https://example.com/report',
    text: async () => '<input value="00000000-0000-0000-0000-000000000001" id="Id" type="hidden">'};
  assert.equal(target.pathname, '/Report/TimeSpanPartial');
  assert.equal(target.searchParams.get('id'), '00000000-0000-0000-0000-000000000001');
  assert.equal(target.searchParams.get('startDate'), '10/07/2026');
  assert.equal(target.searchParams.get('timeOffSet'), CONFIG.timeOffSet);
  assert.equal(target.searchParams.get('showIdle'), 'true');
  assert.equal(target.searchParams.get('allowRemoveTime'), 'False');
  assert.equal(options.headers['X-Requested-With'], 'XMLHttpRequest');
  assert.equal(options.timeout, 30000);
  if (!status) throw new Error('request timed out');
  return {status: () => status, ok: () => status === 200,
          url: () => 'https://example.com' + path, text: async () => html};
}};
(async () => {
  try { console.log(JSON.stringify(await fetchActivity(request, '10/07/2026'))); }
  catch (error) { console.log(JSON.stringify(error.message)); }
})();
"""
    result = subprocess.run(["node", "-e", script, json.dumps([status, url, html])],
                            cwd=ROOT, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == expected


@pytest.mark.skipif(not shutil.which("node"), reason="activity requires Node")
@pytest.mark.parametrize("error,expected", [("當日尚無追蹤資料", 0), ("登入過期", 1)])
def test_activity_login_verifies_session_without_requiring_tracked_time(error, expected):
    script = """
const lib = require('./activity/lib');
lib.fetchActivity = async () => {throw new Error(process.argv[1]);};
const page = {goto: async () => {}, url: () => 'https://example.com/report'};
const Module = require('module');
const load = Module._load;
Module._load = function(name, ...args) {
  if (name === 'playwright') return {chromium: {
    launchPersistentContext: async () => ({pages: () => [page], close: async () => {}}),
  }};
  return load.call(this, name, ...args);
};
require('./activity/login');
"""
    result = subprocess.run(["node", "-e", script, error], cwd=ROOT,
                            capture_output=True, text=True)
    assert result.returncode == expected
    assert ("profile 已存" in result.stdout) is (expected == 0)


@pytest.mark.skipif(not shutil.which("node"), reason="activity requires Node")
@pytest.mark.parametrize("member_id,expected", [
    ("", "45.5"),
    ("00000000-0000-0000-0000-000000000001", "45.5"),
    ("00000000-0000-0000-0000-000000000002", "報表成員與設定不一致，請確認成員 ID 與帳號權限"),
])
def test_activity_env_settings_select_the_requested_member(member_id, expected):
    script = """
const assert = require('node:assert/strict');
const {fetchActivity} = require('./activity/lib');
const request = {get: async (url) => {
  const u = new URL(url);
  assert.equal(u.searchParams.get('timeOffSet'), 'Pacific Standard Time');
  if (u.pathname === '/report') {
    assert.equal(u.searchParams.get('memberId'), process.env.MONITASK_ACTIVITY_MEMBER_ID || null);
    return {status: () => 200, ok: () => true, url: () => url,
      text: async () => '<input id="Id" value="00000000-0000-0000-0000-000000000001">'};
  }
  return {status: () => 200, ok: () => true, url: () => url,
    text: async () => '<div class="timeline__online-widget_medium-text">45.5%</div>'};
}};
(async () => {
  try { console.log(JSON.stringify((await fetchActivity(request, '10/07/2026')).daily)); }
  catch (error) { console.log(JSON.stringify(error.message)); }
})();
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True,
                            text=True, check=True, env={**os.environ,
                                "MONITASK_ACTIVITY_MEMBER_ID": member_id,
                                "MONITASK_ACTIVITY_TIMEZONE": "Pacific Standard Time"})
    assert json.loads(result.stdout) == expected


def _checkout(tmp_path) -> Path:
    """A throwaway checkout: install.sh moves profile/, so it must never run against the real one."""
    repo = tmp_path / "checkout"
    (repo / "activity").mkdir(parents=True)
    for name in ("install.sh", "run.sh", "fetch.js", "lib.js", "login.js", "config.json", "package.json"):
        shutil.copy2(ROOT / "activity" / name, repo / "activity" / name)
    shutil.copytree(ROOT / "src/monitask_center", repo / "src/monitask_center",
                    ignore=shutil.ignore_patterns("__pycache__"))
    return repo


@pytest.mark.parametrize("existing,install_fails", [(True, False), (False, False), (False, True)])
def test_activity_install_prepares_node_before_loading_job(tmp_path, existing, install_fails):
    repo = _checkout(tmp_path)
    tools = tmp_path / "tools"
    tools.mkdir()
    node = tmp_path / "Cellar/node/test-version/bin/node"
    node.parent.mkdir(parents=True)
    if existing:
        node.write_text("#!/bin/sh\necho v22.0.0\n")
        node.chmod(0o755)
    scripts = {
        "uname": "#!/bin/sh\necho Darwin\n",
        "brew": '''#!/bin/bash
case "$1 $2" in
  "list --versions") [ -x "$TEST_NODE" ] ;;
  "install node")
    [ "$TEST_INSTALL_FAILS" = False ] || exit 1
    printf '#!/bin/sh\\necho v22.0.0\\n' > "$TEST_NODE"
    chmod +x "$TEST_NODE"
    echo installed >> "$TEST_LOG" ;;
  "--prefix node") dirname "$(dirname "$TEST_NODE")" ;;
  *) exit 2 ;;
esac
''',
        "launchctl": '#!/bin/sh\necho "$*" >> "$TEST_LOG"\n',
    }
    for name, body in scripts.items():
        path = tools / name
        path.write_text(body)
        path.chmod(0o755)
    log = tmp_path / "commands.log"
    result = subprocess.run(["/bin/bash", str(repo / "activity/install.sh"), "install"],
                            env={**os.environ, "HOME": str(tmp_path),
                                 "MONITASK_CENTER_HOME": str(tmp_path / ".monitask-center"),
                                 "PATH": f"{tools}:/usr/bin:/bin", "TEST_NODE": str(node),
                                 "TEST_INSTALL_FAILS": str(install_fails), "TEST_LOG": str(log)},
                            capture_output=True, text=True)
    plist = tmp_path / "Library/LaunchAgents/com.monitask-center.activity.plist"
    runner = tmp_path / "Library/Application Support/MonitaskCenter/activity/run.sh"
    if install_fails:
        assert result.returncode != 0
        assert not plist.exists() and not log.exists() and not runner.exists()
    else:
        assert result.returncode == 0, result.stderr
        spec = plistlib.loads(plist.read_bytes())
        assert spec["StartCalendarInterval"] == launchd.ACTIVITY_TICKS
        assert spec["EnvironmentVariables"]["MONITASK_ACTIVITY_NODE"] == str(node)
        assert spec["ProgramArguments"] == ["/bin/bash", str(runner), "fetch"]  # the copy, not the checkout
        assert str(node.resolve()) in result.stdout and "不需要「完整磁碟存取權」" in result.stdout
        assert "bootstrap" in log.read_text()
        assert ("installed" in log.read_text()) is (not existing)
        fetched = subprocess.run(["/bin/bash", str(runner), "fetch"],
                                 env={**os.environ, "HOME": str(tmp_path),
                                      "MONITASK_ACTIVITY_NODE": str(node)},
                                 capture_output=True, text=True)
        assert fetched.returncode == 0 and fetched.stdout.strip() == "v22.0.0"


def _sync(repo: Path, home: Path, launchctl_fails: bool = False) -> subprocess.CompletedProcess:
    tools = home / "tools"
    tools.mkdir(exist_ok=True)
    (tools / "launchctl").write_text(f"#!/bin/sh\n[ \"$1\" = bootstrap ] && exit {int(launchctl_fails)}\nexit 0\n")
    (tools / "launchctl").chmod(0o755)
    node = home / "node"
    node.write_text("#!/bin/sh\necho v22.0.0\n")
    node.chmod(0o755)
    return subprocess.run(["/bin/bash", str(repo / "activity/install.sh"), "sync"],
                          env={**os.environ, "HOME": str(home),
                               "MONITASK_CENTER_HOME": str(home / ".monitask-center"),
                               "PATH": f"{tools}:/usr/bin:/bin", "MONITASK_ACTIVITY_NODE": str(node)},
                          capture_output=True, text=True)


def test_activity_sync_moves_the_job_out_of_the_checkout_once(tmp_path):
    repo, home = _checkout(tmp_path), tmp_path / "home"
    home.mkdir(exist_ok=True)
    (repo / "activity/node_modules/playwright").mkdir(parents=True)
    (repo / "activity/node_modules/playwright/index.js").write_text("// stub\n")
    (repo / "activity/profile/Default").mkdir(parents=True)
    (repo / "activity/profile/Default/Cookies").write_text("session")
    (repo / ".env").write_text("TG_CHAT_ID=-100123\nMONITASK_ACTIVITY_TIMEZONE=\"Pacific Standard Time\"\n"
                               "MONITASK_ACTIVITY_MEMBER_ID=from-checkout\nUNRELATED=1\n")
    center = home / ".monitask-center"
    center.mkdir()
    (center / ".env").write_text("MONITASK_ACTIVITY_MEMBER_ID=kept\n")
    act = home / "Library/Application Support/MonitaskCenter/activity"

    first = _sync(repo, home)
    assert first.returncode == 0, first.stderr
    assert (act / "run.sh").is_file() and (act / "node_modules/playwright/index.js").is_file()
    assert (act / "src/monitask_center/config.py").is_file() and not (act / "install.sh").exists()
    # The login session is moved, never copied, and stays owner-only.
    assert (act / "profile/Default/Cookies").read_text() == "session" and not (repo / "activity/profile").exists()
    assert oct(act.stat().st_mode & 0o777) == oct((act / "profile").stat().st_mode & 0o777) == "0o700"
    env = center / ".env"
    assert env.read_text() == ("MONITASK_ACTIVITY_MEMBER_ID=kept\nTG_CHAT_ID=-100123\n"
                               "MONITASK_ACTIVITY_TIMEZONE=\"Pacific Standard Time\"\n")
    assert oct(env.stat().st_mode & 0o777) == "0o600"
    plist = plistlib.loads((home / "Library/LaunchAgents/com.monitask-center.activity.plist").read_bytes())
    assert plist["ProgramArguments"][1] == str(act / "run.sh")
    logged = (home / "Library/Application Support/MonitaskCenter/activity.out").read_text()
    assert "已同步活動率程式" in logged and "已搬移登入 session" in logged and "TG_CHAT_ID" in logged
    assert "-100123" not in logged  # key names only, never values

    # Second run: nothing left to move or merge; a file removed from the checkout leaves the copy too.
    (repo / "activity/login.js").unlink()
    again = _sync(repo, home)
    assert again.returncode == 0, again.stderr
    assert env.read_text().count("TG_CHAT_ID") == 1 and not (act / "login.js").exists()
    assert (act / "profile/Default/Cookies").read_text() == "session"


def test_activity_sync_keeps_both_sessions_when_both_exist(tmp_path):
    repo, home = _checkout(tmp_path), tmp_path / "home"
    home.mkdir(exist_ok=True)
    (repo / "activity/profile").mkdir()
    (repo / "activity/profile/old").write_text("checkout")
    act = home / "Library/Application Support/MonitaskCenter/activity"
    (act / "profile").mkdir(parents=True)
    (act / "profile/new").write_text("copy")
    result = _sync(repo, home)
    assert result.returncode == 0, result.stderr
    assert (repo / "activity/profile/old").is_file() and (act / "profile/new").is_file()
    assert not (act / "profile/old").exists() and "兩處都有登入 session" in result.stdout


def test_activity_sync_reports_a_failed_load(tmp_path):
    repo, home = _checkout(tmp_path), tmp_path / "home"
    home.mkdir(exist_ok=True)
    result = _sync(repo, home, launchctl_fails=True)
    assert result.returncode != 0 and "載入 com.monitask-center.activity 失敗" in result.stdout


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("node"),
                    reason="activity runs on macOS with Node")
@pytest.mark.parametrize("hour,vacation,holiday,enabled,expected", [
    (11, False, False, True, True),
    (12, False, False, True, False),
    (18, False, False, True, True),
    (19, False, False, True, False),
    (11, True, False, True, False),
    (11, False, True, True, False),
    (11, False, False, False, False),
])
def test_activity_window_uses_shared_settings(hour, vacation, holiday, enabled, expected):
    config.save(config.Settings(start_hour=11, telegram=enabled,
                                vacation=("2026-10-07",) if vacation else ()))
    cache = config.home_dir() / "holidays/2026.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps([{"date": "20261007", "isHoliday": holiday}]))
    script = """
const OriginalDate = Date;
global.Date = class extends OriginalDate {
  constructor(...args) { super(...(args.length ? args : [process.argv[1]])); }
};
console.log(JSON.stringify(require('./activity/lib').workWindow()));
"""
    result = subprocess.run(["node", "-e", script, f"2026-10-07T{hour - 8:02d}:00:00Z"],
                            cwd=ROOT, env={**os.environ, "MONITASK_ACTIVITY_PYTHON": sys.executable},
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)["ok"] is expected


@pytest.mark.skipif(not shutil.which("node"), reason="activity requires Node")
@pytest.mark.parametrize("status,receipt,expected", [
    (0, "tg-send: sendMessage OK message_id=123", 0),
    (0, "", 1),  # a disabled bot exits successfully without sending
    (1, "send failed", 1),
])
def test_activity_delivery_requires_a_receipt(status, receipt, expected):
    result = _deliver(str(status), receipt, "65", tty=False)
    assert result.returncode == expected
    assert "今日活動：65%" in result.stdout


@pytest.mark.skipif(not shutil.which("node"), reason="activity requires Node")
@pytest.mark.parametrize("daily,code", [("59.99", "1;31"), ("60", "1;38;5;214"), ("80", "1;32")])
def test_activity_in_a_terminal_prints_the_message_coloured_with_the_receipt_last(daily, code):
    result = _deliver("0", "tg-send: sendMessage OK message_id=123", daily, tty=True)
    assert result.returncode == 0
    lines = result.stdout.splitlines()
    assert lines[0].startswith("\x1b[1;38;5;37m📊 Monitask 活動率 ")
    assert lines[1] == f"今日活動：\x1b[{code}m{daily}%\x1b[0m"
    assert lines[-2] == "\x1b[2m🖥️ Test Host\x1b[0m"
    assert lines[-1] == "\x1b[2mtg-send: sendMessage OK message_id=123\x1b[0m"
    saved = json.loads((config.home_dir() / status.ACTIVITY_FILE).read_text(encoding="utf-8"))
    assert (saved["date"], saved["daily"]) == ("2026-10-07", daily)


@pytest.mark.skipif(not shutil.which("node"), reason="activity requires Node")
@pytest.mark.parametrize("slots,expected", [
    ('<span class="screen_time"> 14:10 </span><span>70% 的 10 分钟。</span>'
     '<span class="screen_time"> 14:20 </span><span>55% 的 5 分钟。</span>',
     "最近紀錄 14:20：55%（5 分鐘）"),
    ("", None),
])
def test_activity_reports_the_latest_slot_with_its_own_time(slots, expected):
    result = _deliver("0", "tg-send: sendMessage OK message_id=123", "65", tty=False,
                      slots=slots)
    assert result.returncode == 0
    assert "最近一小時" not in result.stdout
    if expected:
        assert expected in result.stdout
    else:
        assert "最近紀錄" not in result.stdout


def _deliver(status: str, receipt: str, daily: str, tty: bool,
             slots: str = "", window: dict | None = None) -> subprocess.CompletedProcess:
    script = """
const [status, receipt, daily, tty, slots, window] = process.argv.slice(1);
process.stdout.isTTY = tty === 'true';
const lib = require('./activity/lib');
lib.workWindow = () => JSON.parse(window);
const child = require('child_process');
child.execFileSync = () => 'Test Host';
child.spawnSync = (_cmd, args) => {
  console.error('SENT ' + JSON.stringify(args[2]));
  return {status: Number(status), stderr: receipt};
};
const request = {get: async (url) => ({status: () => 200, ok: () => true,
  url: () => url,
  text: async () => new URL(url).pathname === '/report'
    ? '<input id="Id" value="00000000-0000-0000-0000-000000000001">'
    : `<div class="timeline__online-widget_medium-text">${daily}%</div>${slots}`,
})};
const Module = require('module');
const load = Module._load;
Module._load = function(name, ...args) {
  if (name === 'playwright') return {chromium: {
    launchPersistentContext: async () => ({request, close: async () => {}}),
  }};
  return load.call(this, name, ...args);
};
require('./activity/fetch');
"""
    env = {k: v for k, v in os.environ.items() if k != "NO_COLOR"}
    window = window or {"ok": True, "iso": "2026-10-07", "reportDate": "10/07/2026",
                        "state": str(config.home_dir() / "activity-alert.json")}
    return subprocess.run(["node", "-e", script, status, receipt, daily, str(tty).lower(), slots,
                           json.dumps(window)],
                          cwd=ROOT, capture_output=True, text=True, env=env)


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("node"),
                    reason="activity runs on macOS with Node")
@pytest.mark.parametrize("clock,minutes,report,alert", [
    ("11:00", 10, True, True),
    ("11:03", 10, True, True),     # launchd fired a little late: still this tick
    ("11:10", 10, False, True),
    ("11:15", 10, False, False),   # launchd 5 minutes late: too late for the 11:10 check
    ("11:10", 20, False, False),   # a 10-minute tick between two 20-minute checks
    ("11:20", 20, False, True),
    ("11:40", 40, False, True),    # counted from the 11:00 start, not from the top of the hour
    ("12:00", 40, False, False),
    ("13:40", 40, False, True),    # 160 minutes after 11:00
    ("13:20", 40, False, False),   # 140 minutes: between checks
    ("14:30", 50, False, False),   # 210 minutes: between checks
    ("15:10", 50, False, True),    # 250 minutes after 11:00
    ("11:30", 60, False, False),
    ("09:50", 10, False, False),   # before the start hour
    ("12:20", 10, False, False),   # lunch
    ("18:00", 10, True, True),     # the last check of the day
    ("18:03", 10, True, True),     # ... started a little late: still the 18:00 check
    ("18:10", 10, False, False),
])
def test_activity_alert_ticks_follow_the_check_frequency(clock, minutes, report, alert):
    config.save(config.Settings(start_hour=11, activity_check_minutes=minutes,
                                activity_alert_percent=55))
    cache = config.home_dir() / "holidays/2026.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps([{"date": "20261007", "isHoliday": False}]))
    hour, minute = map(int, clock.split(":"))
    script = """
const OriginalDate = Date;
global.Date = class extends OriginalDate {
  constructor(...args) { super(...(args.length ? args : [process.argv[1]])); }
};
console.log(JSON.stringify(require('./activity/lib').workWindow()));
"""
    result = subprocess.run(["node", "-e", script, f"2026-10-07T{hour - 8:02d}:{minute:02d}:00Z"],
                            cwd=ROOT, env={**os.environ, "MONITASK_ACTIVITY_PYTHON": sys.executable},
                            capture_output=True, text=True, check=True)
    window = json.loads(result.stdout)
    assert (window["ok"], window["alert"]) == (report, alert)
    assert window["threshold"] == 55
    assert window["state"] == str(config.home_dir() / "activity-alert.json")


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("node"),
                    reason="activity runs on macOS with Node")
def test_activity_alert_switched_off_skips_the_check_but_keeps_the_report():
    config.save(config.Settings(start_hour=11, activity_alert=False))
    cache = config.home_dir() / "holidays/2026.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps([{"date": "20261007", "isHoliday": False}]))
    script = """
const OriginalDate = Date;
global.Date = class extends OriginalDate {
  constructor(...args) { super(...(args.length ? args : [process.argv[1]])); }
};
console.log(JSON.stringify(require('./activity/lib').workWindow()));
"""
    result = subprocess.run(["node", "-e", script, "2026-10-07T03:00:00Z"],  # 11:00 Taipei: a report + alert tick
                            cwd=ROOT, env={**os.environ, "MONITASK_ACTIVITY_PYTHON": sys.executable},
                            capture_output=True, text=True, check=True)
    window = json.loads(result.stdout)
    assert (window["ok"], window["alert"]) == (True, False)


SLOT = '<span class="screen_time"> 14:20 </span><span>40% 的 10 分钟。</span>'
RECEIPT = "tg-send: sendMessage OK message_id=123"


def _sent(result: subprocess.CompletedProcess) -> list[str]:
    return [json.loads(line[5:]) for line in result.stderr.splitlines() if line.startswith("SENT ")]


def _alert_window(tmp_path, report=False) -> dict:
    return {"ok": report, "alert": True, "threshold": 60, "iso": "2026-10-07",
            "reportDate": "10/07/2026", "state": str(tmp_path / "activity-alert.json")}


@pytest.mark.skipif(not shutil.which("node"), reason="activity requires Node")
def test_activity_below_threshold_alerts_once_per_slot_with_an_explanation(tmp_path):
    window = _alert_window(tmp_path)
    first = _deliver("0", RECEIPT, "55.2", tty=False, slots=SLOT, window=window)
    assert first.returncode == 0
    [message] = _sent(first)
    assert message.startswith("⚠️ Monitask 活動率偏低")
    assert "今日活動 55.2%，低於提醒門檻 60%（差 4.8 個百分點）" in message
    assert "最近紀錄 14:20：40%（10 分鐘）" in message
    assert "離座" in message and "🖥️ Test Host" in message
    again = _deliver("0", RECEIPT, "55.2", tty=False, slots=SLOT, window=window)
    assert again.returncode == 0 and _sent(again) == []
    newer = SLOT + '<span class="screen_time"> 14:30 </span><span>30% 的 10 分钟。</span>'
    assert len(_sent(_deliver("0", RECEIPT, "54", tty=False, slots=newer, window=window))) == 1


@pytest.mark.skipif(not shutil.which("node"), reason="activity requires Node")
def test_activity_at_or_above_threshold_sends_nothing_on_an_alert_tick(tmp_path):
    result = _deliver("0", RECEIPT, "60", tty=False, slots=SLOT, window=_alert_window(tmp_path))
    assert result.returncode == 0 and _sent(result) == []


@pytest.mark.skipif(not shutil.which("node"), reason="activity requires Node")
def test_activity_failed_alert_delivery_is_retried_next_tick(tmp_path):
    window = _alert_window(tmp_path)
    assert len(_sent(_deliver("1", "send failed", "55", tty=False, slots=SLOT, window=window))) == 1
    assert len(_sent(_deliver("0", RECEIPT, "55", tty=False, slots=SLOT, window=window))) == 1


@pytest.mark.skipif(not shutil.which("node"), reason="activity requires Node")
def test_activity_report_tick_sends_the_report_and_the_alert(tmp_path):
    result = _deliver("0", RECEIPT, "55", tty=False, slots=SLOT, window=_alert_window(tmp_path, True))
    report, alert = _sent(result)
    assert report.startswith("📊 Monitask 活動率") and alert.startswith("⚠️ Monitask 活動率偏低")


@pytest.mark.skipif(not shutil.which("node"), reason="activity requires Node")
def test_activity_fetch_failure_on_an_alert_only_tick_is_logged_not_sent(tmp_path):
    result = _deliver("0", RECEIPT, "101", tty=False, window=_alert_window(tmp_path))
    assert _sent(result) == [] and "報表資料缺少有效的活動率" in result.stdout
