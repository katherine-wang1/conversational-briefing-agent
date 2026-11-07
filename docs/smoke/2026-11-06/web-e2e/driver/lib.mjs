// Driver for the 2026-11-06 browser run against the real backend (results.md). Needs a global
// Playwright (NODE_PATH), the API + worker + `BACKEND_URL=http://localhost:8000 vite` running.
//   OUT=/tmp/web-e2e TOKEN=dev node step1.mjs   (then step2 … step6; SID=<session> for step4)
import fs from "node:fs";
import os from "node:os";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
export const SP = process.env.OUT || `${os.tmpdir()}/web-e2e`; // output: JSON, shots/, browser profile
fs.mkdirSync(`${SP}/shots`, { recursive: true });
export const BASE = process.env.BASE || "http://localhost:5173";
export const STT_STUB = `
(() => {
  class FakeRecognition {
    constructor() { this.onresult = null; this.onerror = null; this.onend = null; this._t = null; }
    start() { window.__sttStarts = (window.__sttStarts || 0) + 1;
      this._t = setTimeout(() => this._emit(false, (window.__sttText||"").split(" ").slice(0,2).join(" ")), 300); }
    _emit(isFinal, text) { const res = [{ transcript: text }]; res.isFinal = isFinal;
      this.onresult && this.onresult({ resultIndex: 0, results: { length: 1, 0: res } }); }
    stop() { clearTimeout(this._t);
      setTimeout(() => { this._emit(true, window.__sttText || "what is that?"); this.onend && this.onend(); }, 50); }
    abort() { clearTimeout(this._t); setTimeout(() => this.onend && this.onend(), 0); }
  }
  window.webkitSpeechRecognition = FakeRecognition; window.SpeechRecognition = FakeRecognition;
})();`;
// Instrument the audio element(s) and WebSocket frames.
export const PROBE = `
(() => {
  window.__audioEv = []; window.__frames = [];
  const P = HTMLMediaElement.prototype; const origPlay = P.play;
  const evs = ["play","playing","pause","ended","error","loadedmetadata","canplay","stalled","waiting"];
  const seen = new WeakSet();
  const hook = (el) => { if (seen.has(el)) return; seen.add(el);
    evs.forEach(e => el.addEventListener(e, () => window.__audioEv.push({t: Date.now(), e, src: (el.currentSrc||el.src||"").slice(-60), ct: el.currentTime, dur: el.duration, err: el.error && el.error.code})));
  };
  P.play = function() { hook(this); return origPlay.apply(this, arguments); };
  const OA = window.Audio; window.Audio = function(...a){ const el = new OA(...a); hook(el); return el; }; window.Audio.prototype = OA.prototype;
  const WS = window.WebSocket;
  window.WebSocket = function(url, p) { const ws = new WS(url, p); window.__wsUrl = url;
    const send = ws.send.bind(ws);
    ws.send = (d) => { try { const j = JSON.parse(d); window.__frames.push({t: Date.now(), dir: "out", type: j.type, j}); } catch { window.__frames.push({t:Date.now(),dir:"out",type:"binary"}); } return send(d); };
    ws.addEventListener("message", (m) => { try { const j = JSON.parse(m.data); window.__frames.push({t: Date.now(), dir: "in", type: j.type, j}); } catch { window.__frames.push({t:Date.now(),dir:"in",type:"binary"}); } });
    return ws; };
  window.WebSocket.prototype = WS.prototype; Object.assign(window.WebSocket, {OPEN:1, CLOSED:3, CONNECTING:0, CLOSING:2});
})();`;
export async function open() {
  const ctx = await chromium.launchPersistentContext(SP + "/profile", {
    headless: true, timezoneId: "America/Los_Angeles", viewport: { width: 390, height: 844 }, deviceScaleFactor: 1,
    permissions: ["microphone"],
    args: ["--autoplay-policy=no-user-gesture-required", "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"],
  });
  await ctx.addInitScript(STT_STUB); await ctx.addInitScript(PROBE);
  const page = ctx.pages()[0] || await ctx.newPage();
  const log = [];
  page.on("pageerror", (e) => { log.push("[pageerror] " + e.message); console.log("[pageerror]", e.message); });
  page.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") { log.push(`[console.${m.type()}] ${m.text()}`); console.log(`[console.${m.type()}]`, m.text().slice(0, 300)); } });
  page.on("response", (r) => { const u = r.url(); if (/\/(api|media|ws)\//.test(u)) { log.push(`[http ${r.status()}] ${r.request().method()} ${u}`); if (!/media/.test(u)) console.log(`[http ${r.status()}] ${r.request().method()} ${u.replace(/^https?:\/\/[^/]+/, "")}`); } });
  return { ctx, page, log };
}
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
