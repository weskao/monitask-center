'use strict';
// 無頭抓 report 頁的今日活動率，工作時窗內每半小時發群組。launchd 進入點：run.sh fetch。
// 參數：force = 略過工作時窗閘門（測試用）。
const { chromium } = require('playwright');
const { execFileSync } = require('child_process');
const os = require('os');
const path = require('path');
const { PROFILE_DIR, workWindow, reportUrl, parseActivity, isLoginPage } = require('./lib');

const TG_SEND = path.join(os.homedir(), '.claude/scripts/tg-send.sh');
const REPO_DIR = path.join(__dirname, '..');
const FORCE = process.argv.includes('force');

function host() {
  try { return execFileSync('/usr/sbin/scutil', ['--get', 'ComputerName'], { encoding: 'utf8' }).trim(); }
  catch (e) { return os.hostname(); }
}

function notify(msg) {
  try {
    execFileSync('/bin/bash', [TG_SEND, 'send', `${msg}\n🖥️ ${host()}`],
      { cwd: REPO_DIR, stdio: 'ignore', timeout: 45000 });
    return true;
  } catch (e) { console.log('tg-send 失敗：', e.message); return false; }
}

function log(m) { console.log(`[${new Date().toISOString()}] ${m}`); }

(async () => {
  const w = workWindow();
  if (!w.ok && !FORCE) { log(`非工作時窗（今日=${w.shift}），略過`); return; }

  const url = reportUrl(w.reportDate);
  const ctx = await chromium.launchPersistentContext(PROFILE_DIR, {
    headless: true,
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    viewport: { width: 1440, height: 900 },
  });
  try {
    const page = ctx.pages()[0] || await ctx.newPage();
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
    // 等今日活動 % 出現（最多 20s）
    await page.waitForFunction(
      () => /^\s*\d+(?:\.\d+)?\s*%\s*$/m.test(document.body.innerText),
      { timeout: 20000 },
    ).catch(() => {});

    const text = await page.evaluate(() => document.body.innerText);
    if (isLoginPage(page.url(), text)) {
      log('session 過期');
      notify('⚠️ Monitask 活動率抓取失敗：登入過期，請在電腦跑 `~/monitask-center/activity/run.sh login` 重登一次。');
      return;
    }
    const a = parseActivity(text);
    if (!a.daily) {
      log('解析不到活動率'); notify('⚠️ Monitask 活動率頁解析不到數字（版面可能改了）。');
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
})();
