'use strict';
// 共用：台北時間、班表判斷、工作時窗、report URL、活動率解析。
const fs = require('fs');
const path = require('path');

const HERE = __dirname;
const CONFIG = JSON.parse(fs.readFileSync(path.join(HERE, 'config.json'), 'utf8'));
const SCHEDULE_FILE = path.join(HERE, '..', 'schedule.json');
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

function prevIso(iso) {
  const dt = new Date(iso + 'T12:00:00Z');
  dt.setUTCDate(dt.getUTCDate() - 1);
  return dt.toISOString().slice(0, 10);
}

function loadSchedule() {
  try {
    const data = JSON.parse(fs.readFileSync(SCHEDULE_FILE, 'utf8'));
    return data.days || data || {};
  } catch (e) { return {}; }
}

function shiftFor(iso) { return loadSchedule()[iso] || 'unknown'; }

// 目前是否在「該通知活動率」的工作時窗；並回報要抓哪一天的 report。
function workWindow() {
  const { year, month, day, hour, minute } = taipeiParts();
  const iso = isoOf(year, month, day);
  const mins = (+hour) * 60 + (+minute);
  const reportDate = `${month}/${day}/${year}`; // 一律抓當前台北日期的 report
  const s = shiftFor(iso);
  if (s === 'early' && mins >= 600 && mins <= 1200) return { ok: true, shift: 'early', iso, reportDate };
  if (s === 'late' && mins >= 1020) return { ok: true, shift: 'late', iso, reportDate };
  // 晚班跨午夜：00:00–01:00 屬前一天的晚班
  if (mins <= 60 && shiftFor(prevIso(iso)) === 'late') return { ok: true, shift: 'late-tail', iso, reportDate };
  return { ok: false, shift: s, iso, reportDate };
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
  CONFIG, PROFILE_DIR, taipeiParts, shiftFor, workWindow, reportUrl,
  parseActivity, isLoginPage,
};
