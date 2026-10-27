'use strict';
// Headlessly fetch today's activity rate from the report page: notify hourly within the work window, and alert
// when it drops below the configured threshold. launchd entry point (every 10 minutes): run.sh fetch.
// Argument: force = send the report now, skipping the work-window gate and the alert (run.sh now / testing).
const { chromium } = require('playwright');
const { execFileSync, spawnSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { PROFILE_DIR, workWindow, fetchActivity, taipeiParts } = require('./lib');

const TG_SEND = path.join(os.homedir(), '.claude/scripts/tg-send.sh');
const REPO_DIR = path.join(__dirname, '..');
const FORCE = process.argv.includes('force');

function host() {
  try { return execFileSync('/usr/sbin/scutil', ['--get', 'ComputerName'], { encoding: 'utf8' }).trim(); }
  catch (e) { return os.hostname(); }
}

// Run from a terminal (`monitask-center activity`): print the message itself, coloured; launchd logs stay plain.
const TTY = Boolean(process.stdout.isTTY);
const COLOR = TTY && !process.env.NO_COLOR;
function paint(text, code) { return COLOR ? `\x1b[${code}m${text}\x1b[0m` : text; }
// Activity rate tone: below 60% red, below 80% amber, otherwise green.
function tone(rate) { return rate < 60 ? '1;31' : rate < 80 ? '1;38;5;214' : '1;32'; }

function notify(msg) {
  const result = spawnSync('/bin/bash', [TG_SEND, 'send', msg],
    { cwd: REPO_DIR, encoding: 'utf8', timeout: 45000 });
  const receipt = (result.stderr || '').match(/tg-send: (?:sendMessage|sendRichMessage) OK message_id=\d+/);
  if (result.status === 0 && receipt) {
    if (TTY) console.log(paint(receipt[0], '2'));
    else log(receipt[0]);
    return true;
  }
  log('tg-send 失敗或未回傳送達確認');
  process.exitCode = 1;
  return false;
}

function log(m) { console.log(`[${new Date().toISOString()}] ${m}`); }

function latestText(a, c = (text) => text) {
  return a.latest ? `最近紀錄 ${a.latest.time}：${c(`${a.latest.rate}%`, tone(a.latest.rate))}（${a.latest.minutes} 分鐘）` : null;
}

// Below the threshold: one explained alert per tracked slot; the slot is remembered only once delivered.
function alertLow(a, w, machine, hour, minute) {
  const daily = Number(a.daily);
  if (!(daily < w.threshold)) return;
  const slot = `${w.iso} ${a.latest ? a.latest.time : ''}`;
  let last = null;
  try { last = JSON.parse(fs.readFileSync(w.state, 'utf8')).slot; } catch (e) { /* first alert */ }
  if (last === slot) { log(`活動率 ${a.daily}% 低於 ${w.threshold}%，本段已提醒，略過`); return; }
  const gap = Number((w.threshold - daily).toFixed(2));
  const message = [
    `⚠️ Monitask 活動率偏低 ${hour}:${minute}`,
    `今日活動 ${a.daily}%，低於提醒門檻 ${w.threshold}%（差 ${gap} 個百分點）`,
    latestText(a),
    '活動率依計時期間的鍵盤與滑鼠操作計算；若正在工作請確認 Monitask 仍在記錄，離座時請暫停計時。',
    `🖥️ ${machine}`,
  ].filter(Boolean).join('\n');
  log(message.replace(/\n/g, ' | '));
  if (!notify(message)) return;
  try {
    fs.mkdirSync(path.dirname(w.state), { recursive: true });
    fs.writeFileSync(w.state, JSON.stringify({ slot }) + '\n');
  } catch (e) { log(`無法記錄已提醒的時段：${e.code || e.name}`); }
}

(async () => {
  const w = workWindow();
  const report = w.ok || FORCE;
  const alert = Boolean(w.alert) && !FORCE;
  if (!report && !alert) { log(`非工作時窗（今日=${w.shift}），略過`); return; }

  const ctx = await chromium.launchPersistentContext(PROFILE_DIR, {
    headless: true,
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    viewport: { width: 1440, height: 900 },
  });
  try {
    let a;
    try {
      a = await fetchActivity(ctx.request, w.reportDate);
    } catch (error) {
      const detail = error.message === '登入過期'
        ? `登入過期，請執行 bash "${path.join(__dirname, 'run.sh')}" login 重登。`
        : error.message;
      log(detail);
      // Only the hourly report tells Telegram: a failure must not repeat on every alert tick.
      if (report) notify(`⚠️ Monitask 活動率抓取失敗：${detail}\n🖥️ ${host()}`);
      process.exitCode = 1;
      return;
    }
    const { hour, minute } = taipeiParts();
    const machine = host();
    // c paints the terminal copy; Telegram gets the same lines unpainted.
    const lines = (c) => [
      c(`📊 Monitask 活動率 ${hour}:${minute}`, '1;38;5;37'),
      `今日活動：${c(`${a.daily}%`, tone(Number(a.daily)))}`,
      latestText(a, c),
      (a.total || a.shots) ? `今日${a.total ? '總時 ' + c(a.total.replace(/\s+/g, ''), '36') : ''}${a.shots ? '　截圖 ' + c(a.shots, '36') : ''}` : null,
      c(`🖥️ ${machine}`, '2'),
    ].filter(Boolean);
    if (report) {
      const plain = lines((text) => text);
      if (TTY) console.log(lines(paint).join('\n'));
      else log(plain.join(' | '));
      notify(plain.join('\n'));
    }
    if (alert) alertLow(a, w, machine, hour, minute);
  } finally {
    await ctx.close();
  }
})().catch((error) => { log(`抓取失敗：${error.name}`); process.exitCode = 1; });
