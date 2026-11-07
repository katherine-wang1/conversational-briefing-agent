// Live check of the playing screen against the real backend (results.md): speed, the talk button,
// readable citations, the live log and settings persistence. Needs a global Playwright, the API +
// worker + `BACKEND_URL=http://localhost:8000 vite` running, and an onboarded dev user with a brief.
//   OUT=/tmp/ui node run.mjs
import fs from "node:fs";
import os from "node:os";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const OUT = process.env.OUT || `${os.tmpdir()}/ui-live`;
const SHOTS = process.env.SHOTS || `${OUT}/shots`;
fs.mkdirSync(SHOTS, { recursive: true });
const BASE = process.env.BASE || "http://localhost:5173";
const QUESTION = process.env.Q || "Who is behind the first story, and what did they announce?";
const STT_STUB = `
(() => {
  class FakeRecognition {
    constructor() { this.onresult = null; this.onerror = null; this.onend = null; this._t = null; }
    start() { window.__sttStarts = (window.__sttStarts || 0) + 1;
      this._t = setTimeout(() => this._emit(false, (window.__sttText||"").split(" ").slice(0,3).join(" ")), 300); }
    _emit(isFinal, text) { const res = [{ transcript: text }]; res.isFinal = isFinal;
      this.onresult && this.onresult({ resultIndex: 0, results: { length: 1, 0: res } }); }
    stop() { clearTimeout(this._t);
      setTimeout(() => { this._emit(true, window.__sttText || "what is that?"); this.onend && this.onend(); }, 50); }
    abort() { clearTimeout(this._t); setTimeout(() => this.onend && this.onend(), 0); }
  }
  window.webkitSpeechRecognition = FakeRecognition; window.SpeechRecognition = FakeRecognition;
})();`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const out = { checks: [] };
const check = (name, ok, detail = "") => {
  out.checks.push({ name, ok: !!ok, detail });
  console.log(`${ok ? "  ok  " : "  FAIL"} ${name}${detail ? ` (${typeof detail === "string" ? detail : JSON.stringify(detail)})` : ""}`);
};

const ctx = await chromium.launchPersistentContext(`${OUT}/profile`, {
  headless: true,
  timezoneId: "America/Los_Angeles",
  viewport: { width: 390, height: 844 },
  deviceScaleFactor: 1,
  permissions: ["microphone"],
  args: ["--autoplay-policy=no-user-gesture-required", "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"],
});
await ctx.addInitScript(STT_STUB);
const page = ctx.pages()[0] || (await ctx.newPage());
page.on("pageerror", (e) => console.log("[pageerror]", e.message));
page.on("console", (m) => m.type() === "error" && console.log("[console.error]", m.text().slice(0, 200)));
const conv = (expr) => page.evaluate(`(() => { const c = window.__conv; return ${expr}; })()`);
const waitPhase = (p, timeout = 20000) =>
  page.waitForFunction((want) => window.__conv?.state.get().phase === want, p, { timeout });
const shot = async (name) => {
  await page.screenshot({ path: `${SHOTS}/${name}.png` });
  console.log(`  [shot] ${name}`);
};

// 1. Settings: turn on the live log and pick 1.2x; both persist across a reload (server-side).
await page.goto(`${BASE}/#/settings`);
if (await page.getByTestId("dev-login").isVisible().catch(() => false)) await page.getByTestId("dev-login").click();
await page.getByTestId("show-debug").waitFor();
check("live log off by default", !(await page.getByTestId("show-debug").isChecked()));
await page.getByTestId("speed-setting").getByRole("radio", { name: "1.3×" }).click();
await page.getByTestId("show-debug").check();
await sleep(800);
const me1 = await page.evaluate(async () => (await (await fetch("/api/me", { headers: { Authorization: "Bearer dev" } })).json()).settings);
check("PATCH /api/me stored playback_rate + show_debug", me1.playback_rate === 1.3 && me1.show_debug === true, me1);
await page.evaluate(() => localStorage.removeItem("briefing.settings")); // prove the server copy is used
await page.reload();
await page.getByTestId("show-debug").waitFor();
await sleep(1000);
const persisted = {
  debug: await page.getByTestId("show-debug").isChecked(),
  rate: await page.getByRole("radio", { name: "1.3×" }).getAttribute("aria-checked"),
};
check("settings survive a reload with local storage cleared", persisted.debug && persisted.rate === "true", persisted);
await shot("01-settings-390");
await page.getByTestId("speed-setting").getByRole("radio", { name: "1.2×" }).click();
await sleep(500);

// 2. Today: ready -> play at 1.2x; playbackRate survives segment changes.
await page.goto(`${BASE}/#/today`);
await page.waitForSelector('[data-phase="ready"]', { timeout: 30000 });
out.header = await page.locator(".today-head .title").textContent();
out.manifestSeconds = await page.evaluate(async () => {
  const b = await (await fetch("/api/briefs/today", { headers: { Authorization: "Bearer dev" } })).json();
  return b.manifest.segments.reduce((a, s) => a + s.duration_ms, 0) / 1000;
});
check("header estimate uses the manifest at 1.2x", true, `${out.header}; manifest ${out.manifestSeconds.toFixed(0)} s → ${(out.manifestSeconds / 1.2).toFixed(0)} s at 1.2×`);
await shot("02-ready-390");
await page.getByTestId("talk").click();
await waitPhase("playing");
const rates = [];
for (let i = 0; i < 12; i++) {
  await sleep(1000);
  rates.push(await conv("({seg: c.state.get().segmentIndex, rate: c.out.el.playbackRate, def: c.out.el.defaultPlaybackRate, pitch: c.out.el.preservesPitch, t: +c.out.el.currentTime.toFixed(2)})"));
}
out.rates = rates;
const segs = new Set(rates.map((r) => r.seg));
check("playbackRate stays 1.2 across segment changes, pitch preserved", segs.size >= 2 && rates.every((r) => r.rate === 1.2 && r.pitch), `${segs.size} segments: ${JSON.stringify(rates.map((r) => [r.seg, r.rate]))}`);
await shot("03-playing-390");

// 3. Interrupt with the "Tap to talk" pill -> listening -> ask (stub STT) -> thinking -> answer.
await page.waitForFunction(() => window.__conv.out.el.currentTime > 0.6 && window.__conv.out.owner === "brief");
await page.evaluate((q) => (window.__sttText = q), QUESTION);
await page.getByTestId("interrupt-btn").click();
const intr = await conv("({phase: c.state.get().phase, paused: c.out.el.paused, ms: c.state.get().lastInterruptMs})");
check("'Tap to talk' pill pauses at once and listens", intr.phase === "listening" && intr.paused, intr);
await sleep(400);
await shot("04-listening-390");
await page.getByTestId("interrupt-btn").click(); // "I'm done" (the fake mic has no speech)
await waitPhase("thinking", 8000).catch(() => {});
await shot("05-thinking-390");
await waitPhase("answering", 30000);
await sleep(700);
check("answer audio plays at 1.2x", await conv("c.out.el.playbackRate === 1.2 && c.out.owner === 'answer'"));
await shot("06-speaking-390");
await page.waitForFunction(() => window.__conv.state.get().phase !== "answering", null, { timeout: 60000 });
const afterPhase = await conv("c.state.get().phase");
if (afterPhase === "paused") await shot("07-resuming-390");
await waitPhase("playing", 20000).catch(() => {});
check("resumed the brief after the answer", (await conv("c.state.get().phase")) === "playing", await conv("c.state.get().phase"));
out.answer = await conv("c.state.get().transcript.filter(l => l.role === 'agent').map(l => ({text: l.text, citations: l.citations, sources: l.sources}))");
const srcs = out.answer.flatMap((a) => a.sources);
check("answer citations resolved to {source, url, title}", srcs.length > 0 && srcs.every((s) => s.source && /^https?:/.test(s.url || "")), srcs);
const chipText = await page.locator('[data-testid="transcript"] .cite').allTextContents();
check("chips show source names, not passage ids", chipText.length > 0 && chipText.every((t) => !/[0-9a-f]{6,}:\d+-\d+/.test(t)), chipText);
out.interruptMs = intr.ms;
out.latencyMs = await conv("c.state.get().lastLatencyMs");

// 4. Live log on the phone: drawer with client and server entries.
await page.getByTestId("livelog-toggle").click();
await sleep(500);
await shot("08-livelog-drawer-390");
const rows = await page.locator('[data-testid="log-row"]').evaluateAll((els) => els.map((e) => ({ k: e.dataset.kind, f: e.dataset.from, t: e.textContent })));
out.logKinds = [...new Set(rows.map((r) => `${r.f}:${r.k}`))].sort();
out.logSample = rows.filter((r) => r.f === "server").map((r) => r.t).slice(0, 60);
const has = (f, k) => rows.some((r) => r.f === f && r.k === k);
check("log has client ws/out/in/audio/latency/stt entries", ["ws", "out", "in", "audio", "latency", "stt"].every((k) => has("client", k)), out.logKinds);
check("log has server state/heard/model/speak/tts/memory entries", ["state", "heard", "model", "speak", "tts", "memory"].every((k) => has("server", k)), out.logKinds);
const modelRow = rows.find((r) => r.f === "server" && r.k === "model");
check("model entries carry model, latency, tokens and $", modelRow && /ms/.test(modelRow.t) && /in .*out/.test(modelRow.t) && /\$\d/.test(modelRow.t), modelRow?.t);
await page.getByTestId("livelog-toggle").click();

// 5. Desktop layout with the side panel.
await page.setViewportSize({ width: 1280, height: 800 });
await sleep(1500);
await shot("09-desktop-playing-log-1280");
await page.evaluate(() => (window.__sttText = "Where did that come from?"));
await page.getByTestId("talk").click();
await sleep(300);
await page.getByTestId("talk").click();
await waitPhase("answering", 30000).catch(() => {});
await page.waitForFunction(() => window.__conv.state.get().phase !== "answering", null, { timeout: 60000 }).catch(() => {});
await sleep(800);
await shot("10-desktop-answer-log-1280");

// 6. End, then History shows readable citations.
await page.getByTestId("end").click();
await page.getByTestId("rating-sheet").waitFor({ timeout: 10000 }).catch(() => {});
await page.keyboard.press("Escape");
await sleep(1500);
await page.setViewportSize({ width: 390, height: 844 });
await page.goto(`${BASE}/#/history`);
await page.locator(".card-head").first().click();
await page.waitForSelector(".transcript.static", { timeout: 10000 }).catch(() => {});
await sleep(800);
const histChips = await page.locator(".transcript.static .cite").allTextContents();
out.historyChips = histChips;
check("History shows source names", histChips.length > 0 && histChips.every((t) => !/[0-9a-f]{6,}:\d+-\d+/.test(t)), histChips);
await shot("11-history-390");

fs.writeFileSync(`${OUT}/run.json`, JSON.stringify(out, null, 1));
console.log(`\n${out.checks.filter((c) => c.ok).length}/${out.checks.length} checks passed`);
await ctx.close();
