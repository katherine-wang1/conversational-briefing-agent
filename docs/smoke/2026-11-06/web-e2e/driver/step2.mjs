import fs from "node:fs";
import { open, sleep, BASE, SP } from "./lib.mjs";
const { ctx, page, log } = await open();
await page.goto(`${BASE}/#/today`);
const t0 = Date.now(); const samples = [];
let shot = false;
while (Date.now() - t0 < 15 * 60e3) {
  const ph = await page.getAttribute('[data-testid="phase"]', "data-phase").catch(() => null);
  const prep = await page.isVisible('[data-testid="preparing"]').catch(() => false);
  samples.push({ t: Math.round((Date.now() - t0) / 1000), ph, prep });
  if (prep && !shot) { await page.screenshot({ path: `${SP}/shots/02-preparing.png` }); shot = true; }
  if (ph === "ready") break;
  await sleep(5000);
}
await sleep(500);
await page.screenshot({ path: `${SP}/shots/03-today-ready.png` });
const out = { secondsToReady: Math.round((Date.now() - t0) / 1000), last: samples.slice(-3), text: (await page.textContent("main")).slice(0, 400), log: log.filter(l => !/200\] GET .*today/.test(l)).slice(-30) };
fs.writeFileSync(`${SP}/step2.json`, JSON.stringify(out, null, 1));
console.log(JSON.stringify(out, null, 1));
await ctx.close();
