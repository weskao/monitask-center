'use strict';
// 首次登入：開「有畫面」的瀏覽器到 Monitask，由你本人手動登入（密碼你自己打）。
// 登入成功後 session 存進 profile/，之後 fetch.js 走無頭沿用。
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
  // 只導這一次；之後「完全不碰頁面」，純被動偵測——避免把你正在打的字沖掉。
  await page.goto(url, { waitUntil: 'domcontentloaded' }).catch(() => {});

  const deadline = Date.now() + 10 * 60 * 1000; // 最多等 10 分鐘
  let ok = false;
  // 第一階段：等你登入（網址離開 /identity/login 即代表登入成功）。期間不做任何導頁。
  while (Date.now() < deadline) {
    let curUrl = '';
    try { curUrl = page.url(); } catch (e) {}
    if (curUrl && !/\/identity\/login/i.test(curUrl)) { ok = true; break; }
    await page.waitForTimeout(2000);
  }
  // 第二階段：登入成功後直接驗證報表資料，不再等待頁面渲染。
  if (ok) {
    try {
      const a = await fetchActivity(ctx.request, `${month}/${day}/${year}`);
      console.log('登入成功 ✅  報表資料驗證：');
      console.log(`  今日活動=${a.daily}%  最近一小時≈${a.lastHour}%  總時=${a.total}  截圖=${a.shots}  段數=${a.blocks.length}`);
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
