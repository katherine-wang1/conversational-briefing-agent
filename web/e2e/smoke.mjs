#!/usr/bin/env node
// Playwright smoke test of the interrupt -> speak -> answer -> resume flow, in desktop Chromium,
// against the mock session server. Uses Chromium's fake mic fed from e2e/fixtures/question.wav
// (synthesised speech: silence, "Who funded that company?", silence) and a stub
// SpeechRecognition (headless Chromium has no Web Speech backend).
//
//   npm run e2e            (HEADED=1 to watch)
//
// Playwright is expected to be installed globally (require('playwright')); it is not a dependency.
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { startMockServer } from "../mock/server.mjs";

const require = createRequire(import.meta.url);
const here = path.dirname(fileURLToPath(import.meta.url));
const webDir = path.resolve(here, "..");
const repoDir = path.resolve(webDir, "..");
const WAV = path.join(here, "fixtures", "question.wav");

let chromium;
try {
  ({ chromium } = require("playwright"));
} catch {
  for (const p of ["/opt/node-tools/node_modules/playwright", process.env.PLAYWRIGHT_MODULE].filter(Boolean)) {
    try {
      ({ chromium } = require(p));
      break;
    } catch {
      /* next */
    }
  }
}
if (!chromium) {
  console.error("playwright not found (expected a global install). Set PLAYWRIGHT_MODULE=/path/to/playwright.");
  process.exit(2);
}

const MOCK_PORT = 8799;
const WEB_PORT = 5199;
const results = [];
let failures = 0;

function check(name, ok, detail = "") {
  results.push({ name, ok, detail });
  if (!ok) failures++;
  console.log(`${ok ? "  ok  " : "  FAIL"} ${name}${detail ? ` (${detail})` : ""}`);
}

// Stub Web Speech API: interim after 300 ms, final on stop().
const STT_STUB = `
(() => {
  class FakeRecognition {
    constructor() { this.onresult = null; this.onerror = null; this.onend = null; this._t = null; }
    start() {
      window.__sttStarts = (window.__sttStarts || 0) + 1;
      this._t = setTimeout(() => this._emit(false, "who funded"), 300);
    }
    _emit(isFinal, text) {
      const res = [{ transcript: text }]; res.isFinal = isFinal;
      this.onresult && this.onresult({ resultIndex: 0, results: { length: 1, 0: res } });
    }
    stop() {
      clearTimeout(this._t);
      setTimeout(() => { this._emit(true, window.__sttText || "who funded that?"); this.onend && this.onend(); }, 50);
    }
    abort() { clearTimeout(this._t); setTimeout(() => this.onend && this.onend(), 0); }
  }
  window.webkitSpeechRecognition = FakeRecognition;
  window.SpeechRecognition = FakeRecognition;
})();`;

async function main() {
  const mock = await startMockServer({ port: MOCK_PORT, audioScale: 0.6, answerDelayMs: 250, quiet: true });
  process.env.BACKEND_URL = `http://localhost:${MOCK_PORT}`;
  const { createServer, preview } = await import("vite");
  // E2E_PREVIEW=1 runs against the production build in dist/ (npm run build first).
  const usePreview = !!process.env.E2E_PREVIEW;
  const vite = usePreview
    ? await preview({
        root: webDir,
        configFile: path.join(webDir, "vite.config.ts"),
        preview: { port: WEB_PORT, strictPort: true, host: "127.0.0.1" },
        logLevel: "warn",
      })
    : await createServer({
        root: webDir,
        configFile: path.join(webDir, "vite.config.ts"),
        server: { port: WEB_PORT, strictPort: true, host: "127.0.0.1" },
        logLevel: "warn",
      });
  if (!usePreview) await vite.listen();
  console.log(`web: ${usePreview ? "production build (vite preview)" : "vite dev server"}`);
  const base = `http://127.0.0.1:${WEB_PORT}`;

  const browser = await chromium.launch({
    headless: !process.env.HEADED,
    args: [
      "--use-fake-ui-for-media-stream",
      "--use-fake-device-for-media-stream",
      `--use-file-for-fake-audio-capture=${WAV}`,
    ],
  });

  try {
    await scenarioBrowserStt(browser, base);
    await scenarioServerStt(browser, base);
    await scenarioSilero(browser, base);
    await scenarioBargeIn(browser, base);
    await scenarioRating(browser, base);
    validateFramesWithPydantic(await (await fetch(`http://localhost:${MOCK_PORT}/__mock/log`)).json());
  } finally {
    await browser.close();
    await vite.close();
    await mock.close();
  }

  console.log(`\n${results.length - failures}/${results.length} checks passed`);
  process.exit(failures ? 1 : 0);
}

async function newPage(browser, base, query) {
  const ctx = await browser.newContext({ permissions: ["microphone"] });
  await ctx.addInitScript(STT_STUB);
  const page = await ctx.newPage();
  page.on("pageerror", (e) => console.log("    [pageerror]", e.message));
  page.on("console", (m) => {
    if (m.type() === "error") console.log("    [console.error]", m.text());
  });
  await page.goto(`${base}/${query}#/today`);
  if (await page.getByTestId("dev-login").isVisible().catch(() => false)) await page.getByTestId("dev-login").click();
  await page.waitForSelector('[data-phase="ready"]', { timeout: 15000 });
  return page;
}

const conv = (page, expr) => page.evaluate(`(() => { const c = window.__conv; return ${expr}; })()`);
const phase = (page) => conv(page, "c.state.get().phase");
const waitPhase = (page, p, timeout = 10000) =>
  page.waitForFunction((want) => window.__conv?.state.get().phase === want, p, { timeout });
const frames = (page) => conv(page, "c.frames.map(f => ({dir: f.dir, msg: f.msg}))");

async function startAndPlay(page, minSegment = 1) {
  await page.getByTestId("talk").click();
  await waitPhase(page, "playing");
  await page.waitForFunction(
    (n) => window.__conv.state.get().segmentIndex >= n && window.__conv.out.el.currentTime > 0.3,
    minSegment,
    { timeout: 15000 },
  );
}

async function tapTalk(page) {
  // pointerdown is what interrupts; a real tap also clicks.
  await page.getByTestId("talk").click();
}

async function scenarioBrowserStt(browser, base) {
  console.log("\n[1] browser STT + energy VAD: interrupt -> speak -> answer -> resume, then interrupt the answer");
  const page = await newPage(browser, base, "?vad=energy&stt=browser");
  await startAndPlay(page, 2);
  check("brief audio is playing on the single audio element", await conv(page, "!c.out.el.paused && c.out.owner === 'brief'"));
  await page.waitForTimeout(2200);
  const playbacks = (await frames(page)).filter((f) => f.dir === "out" && f.msg.type === "playback");
  check("playback reported (segment changes + every 2 s)", playbacks.length >= 3, `${playbacks.length} frames`);

  // Interrupt mid-sentence (not at a segment boundary) so offset_ms is meaningful.
  await page.waitForFunction(() => window.__conv.out.el.currentTime > 0.6 && window.__conv.out.owner === "brief");
  const segBefore = await conv(page, "c.state.get().segmentIndex");
  const sidBefore = await conv(page, `c.player.segments[${segBefore}].sid`);
  await tapTalk(page);
  const afterTap = await conv(page, "({phase: c.state.get().phase, paused: c.out.el.paused, ms: c.state.get().lastInterruptMs})");
  check("tap interrupts instantly (audio paused, listening)", afterTap.paused && afterTap.phase === "listening", `pause took ${afterTap.ms} ms`);
  const intr = (await frames(page)).filter((f) => f.dir === "out" && f.msg.type === "interrupt").at(-1)?.msg;
  check(
    "interrupt frame carries {segment_id, offset_ms}",
    intr && intr.segment_id === sidBefore && Number.isInteger(intr.offset_ms) && intr.offset_ms > 0,
    JSON.stringify(intr),
  );
  check("speech recognition started on interrupt", (await page.evaluate("window.__sttStarts")) === 1);

  await page.waitForFunction(() => window.__conv.state.get().speechDetected, null, { timeout: 8000 });
  check("VAD detected speech onset", true);
  await waitPhase(page, "thinking", 8000);
  check("VAD ended the utterance automatically (no second tap)", true);
  await page.waitForFunction(() => window.__conv.frames.some((f) => f.dir === "out" && f.msg.type === "utterance"));
  const utt = (await frames(page)).filter((f) => f.dir === "out" && f.msg.type === "utterance").at(-1)?.msg;
  check("utterance sent with browser STT final text", utt?.text === "who funded that?" && utt.stt === "browser", JSON.stringify(utt));
  check("mic released after the utterance (stage 1)", await conv(page, "c.stream === null"));
  const eos = await conv(page, "c.state.get().lastInterruptMs");
  check("utterance carries end_of_speech_ts", typeof utt?.end_of_speech_ts === "number", `interrupt->pause ${eos} ms`);

  await waitPhase(page, "answering", 8000);
  check("answer audio plays through the queue", await conv(page, "c.out.owner === 'answer' && !c.out.el.paused"));
  const latency = await conv(page, "c.state.get().lastLatencyMs");
  check("end-of-speech -> first answer audio measured", typeof latency === "number", `${latency} ms against mock`);
  await page.waitForFunction(() => document.querySelector('[data-testid="transcript"]')?.textContent.includes("Halden Ventures"));
  check("agent transcript shown with citations", (await page.locator(".cite").count()) > 0);

  await waitPhase(page, "playing", 15000);
  const segAfter = await conv(page, "c.state.get().segmentIndex");
  check("resume_brief resumes at the start of the interrupted segment", segAfter === segBefore, `seg ${segBefore} -> ${segAfter}`);
  check("resumed from offset 0 of that segment", await conv(page, "c.out.el.currentTime < 1.0"));

  // Interrupt the brief again, then interrupt the ANSWER.
  await page.waitForTimeout(600);
  await page.evaluate(() => (window.__sttText = "which model held up best?"));
  await tapTalk(page);
  await waitPhase(page, "answering", 12000);
  await page.waitForTimeout(150);
  await page.evaluate(() => (window.__sttText = "tell me the source"));
  await tapTalk(page);
  check("answer interrupted: audio stopped, listening again", await conv(page, "c.out.el.paused && c.state.get().phase === 'listening'"));
  const answerIntr = (await frames(page)).filter((f) => f.dir === "out" && f.msg.type === "interrupt").at(-1)?.msg;
  check("answer interrupt sends segment_id null", answerIntr?.segment_id === null, JSON.stringify(answerIntr));
  await waitPhase(page, "answering", 12000);
  await page.waitForFunction(() => document.querySelector('[data-testid="transcript"]')?.textContent.includes("arXiv paper"));
  check("second question answered after interrupting the first answer", true);
  await page.waitForFunction(() => window.__conv.state.get().phase !== "answering", null, { timeout: 15000 });
  const endPhase = await phase(page);
  check("after answering an answer-interrupt, client returns to paused/open floor", ["paused", "open_floor", "playing"].includes(endPhase), endPhase);

  // Skip / next controls.
  if (endPhase === "paused") await page.getByRole("button", { name: "Back to the brief" }).click();
  await waitPhase(page, "playing");
  const before = await conv(page, "c.state.get().storyRef");
  await page.getByTestId("skip").click();
  await page.waitForTimeout(300);
  const after = await conv(page, "c.state.get().storyRef");
  check("skip jumps to the next story", before !== after, `${before} -> ${after}`);

  // Media Session: AirPods/headphone "pause" maps to interrupt.
  await page.evaluate(() => window.__conv.interrupt("media"));
  check("media-session pause handler path interrupts", (await phase(page)) === "listening");
  const hasHandlers = await page.evaluate(() => "mediaSession" in navigator);
  check("Media Session API available in Chromium", hasHandlers);
  await waitPhase(page, "thinking", 8000);
  await page.context().close();
}

async function scenarioServerStt(browser, base) {
  console.log("\n[2] server STT: utterance_audio (MediaRecorder webm/opus)");
  const page = await newPage(browser, base, "?vad=energy&stt=server");
  await startAndPlay(page, 1);
  await tapTalk(page);
  await waitPhase(page, "thinking", 10000);
  await page.waitForFunction(
    () => window.__conv.frames.some((f) => f.dir === "out" && f.msg.type === "utterance_audio"),
    null,
    { timeout: 5000 },
  );
  const ua = (await frames(page)).find((f) => f.msg.type === "utterance_audio")?.msg;
  check("utterance_audio sent", !!ua && ua.format === "webm/opus", ua?.format);
  check("no webkitSpeechRecognition used in server mode", !(await page.evaluate("window.__sttStarts")));
  await page.waitForFunction(() => document.querySelector('[data-testid="transcript"]')?.textContent.includes("(server STT)"));
  check("server-transcribed text replaces the pending user line", true);
  await waitPhase(page, "playing", 15000);
  check("brief resumed after server-STT answer", true);
  await page.context().close();
}

async function scenarioSilero(browser, base) {
  console.log("\n[3] Silero VAD (vad-web) on synthesised speech");
  const page = await newPage(browser, base, "?stt=browser");
  await startAndPlay(page, 1);
  await tapTalk(page);
  await page.waitForFunction(() => window.__conv.state.get().vadEngine !== null, null, { timeout: 15000 });
  const engine = await conv(page, "c.state.get().vadEngine");
  check("Silero model + onnxruntime wasm load from /vad/", engine === "silero", `engine=${engine}`);
  try {
    await page.waitForFunction(() => window.__conv.state.get().speechDetected, null, { timeout: 8000 });
    await page.waitForFunction(() => window.__conv.frames.some((f) => f.msg.type === "utterance"), null, {
      timeout: 8000,
    });
    check("Silero detected the synthetic question and ended it on silence", true);
  } catch {
    check("Silero detected the synthetic question and ended it on silence", false, "timed out");
  }
  await page.context().close();
}

async function scenarioBargeIn(browser, base) {
  console.log("\n[4] stage 2 (headphones mode): voice onset interrupts playback");
  const page = await newPage(browser, base, "?vad=energy&headphones=1");
  await page.getByTestId("talk").click();
  await waitPhase(page, "playing");
  try {
    await page.waitForFunction(() => window.__conv.state.get().phase === "listening", null, { timeout: 8000 });
    check("voice onset (>= 250 ms) interrupted the brief without a tap", true);
  } catch {
    check("voice onset (>= 250 ms) interrupted the brief without a tap", false, await phase(page));
  }
  const intr = (await frames(page)).find((f) => f.msg.type === "interrupt")?.msg;
  check("barge-in interrupt carries the playback position", !!intr?.segment_id, JSON.stringify(intr));
  await waitPhase(page, "answering", 12000).catch(() => {});
  check("barge-in utterance answered", (await phase(page)) === "answering" || (await phase(page)) === "playing");
  check("mic stays open in headphones mode", await conv(page, "c.stream !== null"));
  await page.context().close();
}

async function scenarioRating(browser, base) {
  console.log("\n[5] end session -> rating sheet");
  const page = await newPage(browser, base, "?vad=energy");
  await startAndPlay(page, 0);
  await page.getByTestId("end").click();
  await page.getByTestId("rating-sheet").waitFor();
  await page.getByRole("radio", { name: "4" }).first().click();
  await page.getByRole("button", { name: "Submit" }).click();
  await page.waitForSelector('[data-testid="rating-sheet"]', { state: "detached" });
  const ratings = await (await fetch(`http://localhost:${MOCK_PORT}/__mock/ratings`)).json();
  check("rating posted", ratings.length === 1 && ratings[0].usefulness === 4, JSON.stringify(ratings[0]));
  await page.context().close();
}

function validateFramesWithPydantic(log) {
  console.log("\n[6] protocol frames validated against app/contracts/session.py");
  const all = Object.values(log).flat();
  const errors = all.filter((f) => f.dir === "out" && f.msg.type === "error");
  check("mock reported no contract errors", errors.length === 0, JSON.stringify(errors.slice(0, 3)));
  const client = all.filter((f) => f.dir === "in").map((f) => f.msg);
  const server = all.filter((f) => f.dir === "out").map((f) => f.msg);
  const tmp = path.join(os.tmpdir(), `briefing-frames-${process.pid}.json`);
  fs.writeFileSync(tmp, JSON.stringify({ client, server }));
  const py = `
import json, sys
from pydantic import TypeAdapter
from app.contracts.session import ClientMessage, ServerMessage
d = json.load(open(sys.argv[1]))
c, s = TypeAdapter(ClientMessage), TypeAdapter(ServerMessage)
for m in d["client"]: c.validate_python(m)
for m in d["server"]: s.validate_python(m)
print(len(d["client"]), len(d["server"]))
`;
  const r = spawnSync("python3", ["-c", py, tmp], { cwd: repoDir, encoding: "utf8" });
  fs.rmSync(tmp, { force: true });
  if (r.error || (r.status !== 0 && /No module named/.test(r.stderr))) {
    check("pydantic validation (skipped: python/pydantic unavailable)", true, r.error?.message ?? r.stderr.trim().split("\n").at(-1));
    return;
  }
  check("every client and server frame validates with pydantic", r.status === 0, (r.stdout || r.stderr).trim().split("\n").at(-1));
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
