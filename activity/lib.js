'use strict';
// 共用：台北時間、工作時窗、report URL、活動率解析。
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const HERE = __dirname;
const CONFIG = JSON.parse(fs.readFileSync(path.join(HERE, 'config.json'), 'utf8'));
const PROFILE_DIR = path.join(HERE, 'profile');

function taipeiParts() {
  const fmt = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Taipei', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  });
  const p = {};
  for (const x of fmt.formatToParts(new Date())) if (x.type !== 'literal') p[x.type] = x.value;
  let hour = p.hour === '24' ? '00' : p.hour;
  return { year: p.year, month: p.month, day: p.day, hour, minute: p.minute };
}

function isoOf(y, m, d) { return `${y}-${m}-${d}`; }

// 目前是否在「該通知活動率」的工作時窗；並回報要抓哪一天的 report。
function workWindow() {
  const { year, month, day, hour } = taipeiParts();
  const iso = isoOf(year, month, day);
  const root = path.join(HERE, '..');
  // Reuse the timer checker's settings and holiday calendar, not a second schedule.
  const script = `import json, sys
from datetime import date
from monitask_center import config, workday
s = config.load()
h = int(sys.argv[2])
ok = s.telegram and h in s.check_hours() and not s.is_lunch(h)
reason = "outside"
if ok:
    ok, reason = workday.peek_status(date.fromisoformat(sys.argv[1]), s)
print(json.dumps({"ok": ok, "shift": reason}))`;
  const result = execFileSync(path.join(root, 'MonitaskCenter.app/Contents/MacOS/python-runner'),
    ['-c', script, iso, hour], { encoding: 'utf8', timeout: 15000,
      env: { ...process.env, PYTHONPATH: `${path.join(root, 'src')}:${path.join(root, 'pylibs')}` } });
  return { ...JSON.parse(result), iso, reportDate: `${month}/${day}/${year}` };
}

function reportUrl(reportDate) {
  const off = encodeURIComponent(CONFIG.timeOffSet);
  return `https://app.monitask.com/report?memberId=${CONFIG.memberId}` +
    `&date=${reportDate}&timeOffSet=${off}&showIdle=true&project=&order=hour_asc`;
}

// 從 report 頁 innerText 解析活動率。回傳 {daily, lastHour, blocks, total, shots}。
function parseActivity(text) {
  const daily = (text.match(/^\s*(\d+(?:\.\d+)?)\s*%\s*$/m) || [])[1] || null;
  const blocks = [...text.matchAll(/(\d+)\s*%\s*的\s*\d+\s*分/g)].map((x) => +x[1]);
  const lastN = blocks.slice(-6);
  const lastHour = lastN.length ? Math.round(lastN.reduce((a, b) => a + b, 0) / lastN.length) : null;
  const total = (text.match(/(\d+\s*H\s*\d+\s*米)/) || [])[1] || null;
  const shots = (text.match(/(\d+)\s*截图/) || [])[1] || null;
  return { daily, lastHour, blocks, total, shots };
}

function isLoginPage(url, text) {
  return /\/identity\/login/i.test(url) || /Log In|登入|登录/.test(text || '');
}

module.exports = {
  CONFIG, PROFILE_DIR, taipeiParts, workWindow, reportUrl,
  parseActivity, isLoginPage,
};
