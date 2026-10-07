"""The activity reporter must use the same hours and days as the timer checker."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from monitask_center import config

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("node"),
                    reason="activity uses the macOS bundled runtime and Node")
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
                            cwd=ROOT, env=os.environ.copy(), capture_output=True,
                            text=True, check=True)
    assert json.loads(result.stdout)["ok"] is expected


@pytest.mark.skipif(not shutil.which("node"), reason="activity requires Node")
@pytest.mark.parametrize("status,receipt,expected", [
    (0, "tg-send: sendMessage OK message_id=123", 0),
    (0, "", 1),  # a disabled bot exits successfully without sending
    (1, "send failed", 1),
])
def test_activity_delivery_requires_a_receipt(status, receipt, expected):
    script = """
const lib = require('./activity/lib');
lib.workWindow = () => ({ok: true, reportDate: '10/07/2026'});
const child = require('child_process');
child.execFileSync = () => 'Test Host';
child.spawnSync = () => ({status: Number(process.argv[1]), stderr: process.argv[2]});
const page = {
  goto: async () => {}, waitForFunction: async () => {},
  evaluate: async () => '65%', url: () => 'https://example.com/report',
};
const Module = require('module');
const load = Module._load;
Module._load = function(name, ...args) {
  if (name === 'playwright') return {chromium: {
    launchPersistentContext: async () => ({pages: () => [page], close: async () => {}}),
  }};
  return load.call(this, name, ...args);
};
require('./activity/fetch');
"""
    result = subprocess.run(["node", "-e", script, str(status), receipt], cwd=ROOT,
                            capture_output=True, text=True)
    assert result.returncode == expected
    assert "今日活動：65%" in result.stdout
