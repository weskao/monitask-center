"""The activity reporter must use the same hours and days as the timer checker."""

import json
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from monitask_center import config

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


@pytest.mark.parametrize("existing,install_fails", [(True, False), (False, False), (False, True)])
def test_activity_install_prepares_node_before_loading_job(tmp_path, existing, install_fails):
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
    result = subprocess.run(["/bin/bash", str(ROOT / "activity/install.sh"), "install"],
                            env={**os.environ, "HOME": str(tmp_path),
                                 "PATH": f"{tools}:/usr/bin:/bin", "TEST_NODE": str(node),
                                 "TEST_INSTALL_FAILS": str(install_fails), "TEST_LOG": str(log)},
                            capture_output=True, text=True)
    plist = tmp_path / "Library/LaunchAgents/com.monitask-center.activity.plist"
    if install_fails:
        assert result.returncode != 0
        assert not plist.exists() and not log.exists()
    else:
        assert result.returncode == 0, result.stderr
        spec = plistlib.loads(plist.read_bytes())
        assert spec["EnvironmentVariables"]["MONITASK_ACTIVITY_NODE"] == str(node)
        assert str(node.resolve()) in result.stdout and "完整磁碟存取權" in result.stdout
        assert "bootstrap" in log.read_text()
        assert ("installed" in log.read_text()) is (not existing)
        runner = tmp_path / "activity/run.sh"
        runner.parent.mkdir()
        shutil.copy(ROOT / "activity/run.sh", runner)
        fetched = subprocess.run(["/bin/bash", str(runner), "fetch"],
                                 env={**os.environ, "HOME": str(tmp_path),
                                      "MONITASK_ACTIVITY_NODE": str(node)},
                                 capture_output=True, text=True)
        assert fetched.returncode == 0 and fetched.stdout.strip() == "v22.0.0"


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
             slots: str = "") -> subprocess.CompletedProcess:
    script = """
const [status, receipt, daily, tty, slots] = process.argv.slice(1);
process.stdout.isTTY = tty === 'true';
const lib = require('./activity/lib');
lib.workWindow = () => ({ok: true, reportDate: '10/07/2026'});
const child = require('child_process');
child.execFileSync = () => 'Test Host';
child.spawnSync = () => ({status: Number(status), stderr: receipt});
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
    return subprocess.run(["node", "-e", script, status, receipt, daily, str(tty).lower(), slots],
                          cwd=ROOT, capture_output=True, text=True, env=env)
