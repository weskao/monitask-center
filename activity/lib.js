'use strict';
// Shared: Taipei time, work window, report URL, activity-rate parsing.
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const HERE = __dirname;
const CONFIG = JSON.parse(fs.readFileSync(path.join(HERE, 'config.json'), 'utf8'));
CONFIG.memberId = process.env.MONITASK_ACTIVITY_MEMBER_ID || CONFIG.memberId || '';
CONFIG.timeOffSet = process.env.MONITASK_ACTIVITY_TIMEZONE || CONFIG.timeOffSet;
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

// Whether we are now inside the work window when the activity rate should be notified, and which day's report to fetch.
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
  // The runtime `monitask-center install` builds: its python copy, plus pylibs for telegram-kit.
  const runtime = path.join(require('os').homedir(), 'Library/Application Support/MonitaskCenter');
  const python = process.env.MONITASK_ACTIVITY_PYTHON
    || path.join(runtime, 'MonitaskCenter.app/Contents/MacOS/python-runner');
  const result = execFileSync(python, ['-c', script, iso, hour], { encoding: 'utf8', timeout: 15000,
    env: { ...process.env, PYTHONPATH: `${path.join(root, 'src')}:${path.join(runtime, 'pylibs')}` } });
  return { ...JSON.parse(result), iso, reportDate: `${month}/${day}/${year}` };
}

function reportUrl(reportDate) {
  const params = new URLSearchParams({ date: reportDate, timeOffSet: CONFIG.timeOffSet,
    showIdle: 'true', project: '', order: 'hour_asc' });
  if (CONFIG.memberId) params.set('memberId', CONFIG.memberId);
  return `https://app.monitask.com/report?${params}`;
}

// Parse the activity rate from the report page's innerText. Returns {daily, blocks, total, shots}.
function parseActivity(text) {
  const daily = (text.match(/^\s*(\d+(?:\.\d+)?)\s*%\s*$/m) || [])[1] || null;
  const blocks = [...text.matchAll(/(\d+)\s*%\s*的\s*\d+\s*分/g)].map((x) => +x[1]);
  const total = (text.match(/(\d+\s*H\s*\d+\s*米)/) || [])[1] || null;
  const shots = (text.match(/(\d+)\s*截图/) || [])[1] || null;
  return { daily, blocks, total, shots };
}

// The newest tracked 10-minute slot (order=hour_asc puts it last): {time, rate, minutes} or null.
function latestSlot(html) {
  const slots = [...html.matchAll(/\bscreen_time\b[^>]*>\s*(\d{1,2}:\d{2})\s*<(?:(?!screen_time)[\s\S])*?(\d+)\s*%\s*的\s*(\d+)\s*分/g)];
  const last = slots[slots.length - 1];
  return last ? { time: last[1], rate: +last[2], minutes: +last[3] } : null;
}

async function reportResponse(request, url) {
  let response;
  try {
    response = await request.get(url, {
      headers: { 'X-Requested-With': 'XMLHttpRequest' }, timeout: 30000,
    });
  } catch (error) {
    // Playwright errors contain the request URL; do not log member IDs or cookies.
    throw new Error('報表請求失敗（網路錯誤或逾時）');
  }
  if (response.status() === 401 || isLoginPage(response.url(), '')) {
    throw new Error('登入過期');
  }
  if (!response.ok()) throw new Error(`報表請求失敗（HTTP ${response.status()}）`);
  return response.text();
}

async function fetchActivity(request, reportDate) {
  const report = await reportResponse(request, reportUrl(reportDate));
  // The report can select a different member than the configured URL (e.g. a stale ID).
  const input = report.match(/<input\b[^>]*\bid=["']Id["'][^>]*>/i)?.[0] || '';
  const id = input.match(/\bvalue=["']([a-f\d]{8}-(?:[a-f\d]{4}-){3}[a-f\d]{12})["']/i)?.[1];
  if (!id) throw new Error('報表資料缺少成員識別碼');
  if (CONFIG.memberId && id.toLowerCase() !== CONFIG.memberId.toLowerCase()) {
    throw new Error('報表成員與設定不一致，請確認成員 ID 與帳號權限');
  }
  const params = new URLSearchParams({ id, startDate: reportDate,
    timeOffSet: CONFIG.timeOffSet, allowRemoveTime: 'False', showIdle: 'true',
    project: '', order: 'hour_asc' });
  const html = (await reportResponse(request, `https://app.monitask.com/Report/TimeSpanPartial?${params}`))
    .replace(/<(script|style)\b[^>]*>[\s\S]*?<\/\1>/gi, '');
  if (/这一天没有时间被跟踪|No time (?:was |has been )?tracked/i.test(html)) {
    throw new Error('當日尚無追蹤資料');
  }
  // shortcut: this HTML endpoint is internal; update if its metric markup changes.
  const daily = (html.match(/<div\b[^>]*class=["'][^"']*\btimeline__online-widget_medium-text\b[^"']*["'][^>]*>\s*(\d+(?:\.\d+)?)\s*%\s*<\/div>/i) || [])[1];
  if (daily == null || Number(daily) > 100) throw new Error('報表資料缺少有效的活動率');
  const text = html.replace(/<[^>]*>/g, '').replace(/&nbsp;|&#160;/g, ' ');
  const total = (html.match(/<div\b[^>]*class=["'][^"']*\btimeline__online-widget_online-text\b[^"']*["'][^>]*>\s*([^<]+)<\/div>/i) || [])[1]?.trim().replace('米', '分') || null; // page renders minutes as 米 (mistranslated "M")
  const shotHtml = (html.match(/<a\b[^>]*class=["'][^"']*\ball-screenshots-link\b[^"']*["'][^>]*>([\s\S]*?)<\/a>/i) || [])[1] || '';
  const shots = shotHtml.replace(/<[^>]*>/g, '').match(/\d+/)?.[0] || null;
  return { ...parseActivity(text), latest: latestSlot(html), daily, total, shots };
}

function isLoginPage(url, text) {
  return /\/identity\/login/i.test(url) || /Log In|登入|登录/.test(text || '');
}

module.exports = {
  CONFIG, PROFILE_DIR, taipeiParts, workWindow, reportUrl,
  parseActivity, isLoginPage, fetchActivity,
};
