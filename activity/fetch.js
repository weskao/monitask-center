'use strict';
// 無頭抓 report 頁的今日活動率，工作時窗內每小時通知。launchd 進入點：run.sh fetch。
// 參數：force = 略過工作時窗閘門（測試用）。
const { chromium } = require('playwright');
const { execFileSync, spawnSync } = require('child_process');
const os = require('os');
const path = require('path');
const { PROFILE_DIR, workWindow, fetchActivity } = require('./lib');

const TG_SEND = path.join(os.homedir(), '.claude/scripts/tg-send.sh');
const REPO_DIR = path.join(__dirname, '..');
const FORCE = process.argv.includes('force');

function host() {
  try { return execFileSync('/usr/sbin/scutil', ['--get', 'ComputerName'], { encoding: 'utf8' }).trim(); }
  catch (e) { return os.hostname(); }
}

function notify(msg) {
  const result = spawnSync('/bin/bash', [TG_SEND, 'send', `${msg}\n🖥️ ${host()}`],
    { cwd: REPO_DIR, encoding: 'utf8', timeout: 45000 });
  const receipt = (result.stderr || '').match(/tg-send: (?:sendMessage|sendRichMessage) OK message_id=\d+/);
  if (result.status === 0 && receipt) {
    log(receipt[0]);
    return true;
  }
  log('tg-send 失敗或未回傳送達確認');
  process.exitCode = 1;
  return false;
}

function log(m) { console.log(`[${new Date().toISOString()}] ${m}`); }

(async () => {
  const w = workWindow();
  if (!w.ok && !FORCE) { log(`非工作時窗（今日=${w.shift}），略過`); return; }

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
      notify(`⚠️ Monitask 活動率抓取失敗：${detail}`);
      process.exitCode = 1;
      return;
    }
    const { hour, minute } = require('./lib').taipeiParts();
    const lines = [
      `📊 Monitask 活動率 ${hour}:${minute}`,
      `今日活動：${a.daily}%`,
      a.lastHour != null ? `最近一小時：≈${a.lastHour}%（近${Math.min(a.blocks.length, 6)}段）` : null,
      (a.total || a.shots) ? `今日${a.total ? '總時 ' + a.total.replace(/\s+/g, '') : ''}${a.shots ? '　截圖 ' + a.shots : ''}` : null,
    ].filter(Boolean);
    log(lines.join(' | '));
    notify(lines.join('\n'));
  } finally {
    await ctx.close();
  }
})().catch((error) => { log(`抓取失敗：${error.name}`); process.exitCode = 1; });
