import fs from "node:fs";
import { open, sleep, BASE, SP } from "./lib.mjs";
const { ctx, page, log } = await open();
const out = {};
await page.goto(`${BASE}/#/today`);
await page.getByTestId("dev-login").click();
await sleep(2500);
out.routeAfterLogin = await page.evaluate(() => location.hash);
out.bodyAfterLogin = (await page.textContent("body")).slice(0, 400);
await page.screenshot({ path: `${SP}/shots/01-onboarding.png` });
// brief before onboarding, as the app would call it
out.todayBefore = await page.evaluate(async (t) => { const r = await fetch("/api/briefs/today", { headers: { Authorization: "Bearer " + t } }); return { status: r.status, body: await r.text() }; }, process.env.TOKEN || "dev");
await page.fill("textarea", process.env.INTERESTS || "AI agents and agent memory research, grid-scale battery storage, humanoid robots");
await page.getByRole("button", { name: "~2 min" }).click();
await page.getByRole("button", { name: "Start" }).click();
const t0 = Date.now();
const samples = [];
for (let i = 0; i < 8; i++) { await sleep(1000); samples.push({ t: Date.now() - t0, hash: await page.evaluate(() => location.hash), text: (await page.textContent("main")).slice(0, 200) }); }
out.samples = samples;
await page.screenshot({ path: `${SP}/shots/02-preparing.png` });
out.log = log;
fs.writeFileSync(`${SP}/step1.json`, JSON.stringify(out, null, 1));
console.log(JSON.stringify({ ...out, log: undefined }, null, 1));
await ctx.close();
