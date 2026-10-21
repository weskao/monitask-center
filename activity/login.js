'use strict';
// First login: open a visible browser to Monitask and log in by hand (type the password yourself).
// After a successful login the session is saved in profile/, which fetch.js reuses headlessly.
const { chromium } = require('playwright');
const { PROFILE_DIR, reportUrl, taipeiParts, fetchActivity } = require('./lib');

(async () => {
  const { year, month, day } = taipeiParts();
  const url = reportUrl(`${month}/${day}/${year}`);
  const ctx = await chromium.launchPersistentContext(PROFILE_DIR, {
    headless: false,
    viewport: null,
    args: ['--no-first-run'],
  });
  const page = ctx.pages()[0] || await ctx.newPage();
  console.log('開啟 Monitask… 請在跳出的視窗自行登入（帳號已預填，密碼你自己打）。');
  // Navigate only once; afterwards never touch the page and only observe passively, so what you are typing is not wiped.
  await page.goto(url, { waitUntil: 'domcontentloaded' }).catch(() => {});

  const deadline = Date.now() + 10 * 60 * 1000; // wait at most 10 minutes
  let ok = false;
  // Phase 1: wait for you to log in (leaving /identity/login means success). No navigation in the meantime.
  while (Date.now() < deadline) {
    let curUrl = '';
    try { curUrl = page.url(); } catch (e) {}
    if (curUrl && !/\/identity\/login/i.test(curUrl)) { ok = true; break; }
    await page.waitForTimeout(2000);
  }
  // Phase 2: after login, verify the report data directly without waiting for the page to render.
  if (ok) {
    try {
      const a = await fetchActivity(ctx.request, `${month}/${day}/${year}`);
      console.log('登入成功 ✅  報表資料驗證：');
      const latest = a.latest ? `${a.latest.time} ${a.latest.rate}%` : '無';
      console.log(`  今日活動=${a.daily}%  最近紀錄=${latest}  總時=${a.total}  截圖=${a.shots}  段數=${a.blocks.length}`);
    } catch (error) {
      if (error.message === '當日尚無追蹤資料') {
        console.log('登入成功 ✅  當日尚無追蹤資料。');
      } else {
        console.log(`報表資料驗證失敗：${error.message}`);
        process.exitCode = 1;
      }
    }
  }
  await ctx.close();
  if (!ok) { console.log('逾時未偵測到登入成功。請重跑 login 再登一次。'); process.exit(1); }
  if (!process.exitCode) console.log('profile 已存，之後 fetch 直接讀取報表資料。');
})();
