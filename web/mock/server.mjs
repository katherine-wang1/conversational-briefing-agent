#!/usr/bin/env node
// Mock backend for web development: the REST endpoints of TDD §11 that the app uses, the
// /ws/session/{id} protocol of TDD §12.5 (app/contracts/session.py), and fake TTS audio.
//
//   node mock/server.mjs              # port 8787
//   MOCK_PORT=9000 MOCK_AUDIO_SCALE=0.3 MOCK_ANSWER_DELAY_MS=200 node mock/server.mjs
//
// Debug: GET /__mock/log returns every WebSocket frame (client and server) per session.
import http from "node:http";
import { fileURLToPath } from "node:url";
import { WebSocketServer } from "ws";
import { durationFor, fakeMp3 } from "./fakeAudio.mjs";
import {
  answerFor,
  BRIEF_ID,
  briefScript,
  history,
  initialInterests,
  initialQuestions,
  transcript,
} from "./fixtures.mjs";

// Field lists mirrored from app/contracts/session.py (extra="forbid"): reject unknown fields so
// the client can't drift from the contract without the mock noticing.
const CLIENT_FIELDS = {
  hello: ["type", "session_id", "client"],
  playback: ["type", "segment_id", "offset_ms", "completed"],
  interrupt: ["type", "segment_id", "offset_ms"],
  utterance: ["type", "text", "stt", "end_of_speech_ts"],
  utterance_audio: ["type", "format", "data_b64", "end_of_speech_ts"],
  control: ["type", "action"],
};
const REQUIRED = {
  hello: ["session_id"],
  playback: ["segment_id", "offset_ms"],
  interrupt: [],
  utterance: ["text"],
  utterance_audio: ["format", "data_b64"],
  control: ["action"],
};
const CONTROL_ACTIONS = ["skip", "next", "resume", "end", "play"];

export function validateClientMessage(m) {
  if (!m || typeof m !== "object") return "not an object";
  const fields = CLIENT_FIELDS[m.type];
  if (!fields) return `unknown type ${m.type}`;
  const extra = Object.keys(m).filter((k) => !fields.includes(k));
  if (extra.length) return `unknown fields ${extra.join(",")}`;
  const missing = REQUIRED[m.type].filter((k) => m[k] === undefined || m[k] === null);
  if (missing.length) return `missing ${missing.join(",")}`;
  if (m.type === "control" && !CONTROL_ACTIONS.includes(m.action)) return `bad action ${m.action}`;
  if (m.type === "playback" && !Number.isInteger(m.offset_ms)) return "offset_ms must be int";
  if (m.type === "interrupt" && m.offset_ms !== undefined && !Number.isInteger(m.offset_ms))
    return "offset_ms must be int";
  return null;
}

export function startMockServer({
  port = Number(process.env.MOCK_PORT ?? 8787),
  audioScale = Number(process.env.MOCK_AUDIO_SCALE ?? 1),
  answerDelayMs = Number(process.env.MOCK_ANSWER_DELAY_MS ?? 400),
  quiet = false,
} = {}) {
  const log = (...a) => quiet || console.log("[mock]", ...a);
  const script = briefScript();
  const manifest = {
    brief_id: BRIEF_ID,
    segments: script.sentences.map((s) => ({
      sid: s.sid,
      url: `/audio/${BRIEF_ID}/${s.sid}.mp3`,
      duration_ms: durationFor(s.text, audioScale),
      story_ref: s.story_ref,
    })),
  };

  const db = {
    me: {
      user_id: "user-mock",
      email: "dev@example.com",
      display_name: "Dev",
      onboarded: process.env.MOCK_ONBOARDED !== "false",
      research_consent: false,
      settings: {
        brief_time: "07:30",
        timezone: "America/Los_Angeles",
        voice: "default",
        stt_mode: "browser",
        auto_resume: true,
        headphones_mode: false,
      },
    },
    interests: initialInterests(),
    questions: initialQuestions(),
    ratings: [],
    sessions: new Map(), // id -> { frames: [] }
  };
  let sessionCounter = 0;

  const json = (res, code, body) => {
    res.writeHead(code, { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" });
    res.end(body === undefined ? "" : JSON.stringify(body));
  };
  const readBody = (req) =>
    new Promise((resolve) => {
      let d = "";
      req.on("data", (c) => (d += c));
      req.on("end", () => {
        try {
          resolve(d ? JSON.parse(d) : {});
        } catch {
          resolve({});
        }
      });
    });

  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, "http://x");
    const p = url.pathname;
    const m = req.method;
    if (m === "OPTIONS") {
      res.writeHead(204, {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Authorization, Content-Type",
        "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE",
      });
      return res.end();
    }
    if (p === "/healthz") return json(res, 200, { ok: true });

    // Fake brief audio.
    const audio = p.match(/^\/audio\/([^/]+)\/([^/]+)\.mp3$/);
    if (audio) {
      const seg = manifest.segments.find((s) => s.sid === audio[2]);
      if (!seg) return json(res, 404, { detail: "no such segment" });
      const buf = fakeMp3(`${audio[1]}/${seg.sid}`, seg.duration_ms, "brief");
      res.writeHead(200, { "Content-Type": "audio/mpeg", "Content-Length": buf.length, "Cache-Control": "no-cache" });
      return res.end(buf);
    }

    if (p === "/__mock/log") {
      const out = {};
      for (const [id, s] of db.sessions) out[id] = s.frames;
      return json(res, 200, out);
    }
    if (p === "/__mock/ratings") return json(res, 200, db.ratings);

    if (!p.startsWith("/api/")) return json(res, 404, { detail: "not found" });
    if (!req.headers.authorization?.startsWith("Bearer ")) return json(res, 401, { detail: "missing bearer token" });

    if (p === "/api/me" && m === "GET") return json(res, 200, db.me);
    if (p === "/api/me" && m === "PATCH") {
      // Real backend shape (MePatch): profile fields flat, client settings under `settings`.
      const body = await readBody(req);
      if ("research_consent" in body) db.me.research_consent = !!body.research_consent;
      for (const k of ["brief_time", "timezone"]) if (k in body) db.me.settings[k] = body[k];
      db.me.settings = { ...db.me.settings, ...(body.settings ?? {}) };
      return json(res, 200, db.me);
    }
    if (p === "/api/onboarding" && m === "POST") {
      const body = await readBody(req);
      db.me.onboarded = true;
      db.me.research_consent = !!body.research_consent;
      db.me.settings.brief_time = body.brief_time ?? db.me.settings.brief_time;
      db.me.settings.timezone = body.timezone ?? db.me.settings.timezone;
      for (const [i, label] of String(body.interests_text ?? "")
        .split(/[,;\n]/)
        .map((x) => x.trim())
        .filter(Boolean)
        .entries())
        db.interests.push({
          id: `onb-${Date.now()}-${i}`,
          label,
          subtopic: "other",
          kind: "topic",
          weight: 0.5,
          confidence: 0.3,
          explicit_pin: false,
          source: "onboarding",
        });
      return json(res, 202, { status: "seeding", next: "/api/briefs/today" });
    }
    if (p === "/api/briefs/today" && m === "GET") {
      if (!db.me.onboarded) return json(res, 409, { status: "onboarding_required", local_date: script.local_date });
      return json(res, 200, { brief_id: BRIEF_ID, status: "ready", script, manifest });
    }
    if (p === "/api/sessions" && m === "POST") {
      const id = `sess-${++sessionCounter}`;
      db.sessions.set(id, { frames: [] });
      return json(res, 201, { session_id: id, ws_url: `ws://localhost:${port}/ws/session/${id}` });
    }
    if (p === "/api/history" && m === "GET") return json(res, 200, history());
    const tr = p.match(/^\/api\/sessions\/([^/]+)\/transcript$/);
    if (tr && m === "GET") {
      const t = transcript(decodeURIComponent(tr[1]));
      return t ? json(res, 200, t) : json(res, 404, { detail: "not_found" });
    }
    if (p === "/api/memory/interests" && m === "GET") return json(res, 200, { interests: db.interests });
    const interest = p.match(/^\/api\/memory\/interests\/(.+)$/);
    if (interest) {
      const id = decodeURIComponent(interest[1]);
      const it = db.interests.find((x) => x.id === id);
      if (!it) return json(res, 404, { detail: "no such interest" });
      if (m === "DELETE") {
        db.interests = db.interests.filter((x) => x.id !== id);
        return json(res, 204);
      }
      if (m === "PATCH") {
        const body = await readBody(req);
        for (const k of ["label", "weight", "explicit_pin"]) if (k in body) it[k] = body[k];
        return json(res, 200, it);
      }
    }
    if (p === "/api/questions" && m === "GET") return json(res, 200, { questions: db.questions });
    const q = p.match(/^\/api\/questions\/(.+)$/);
    if (q && m === "PATCH") {
      const it = db.questions.find((x) => x.id === decodeURIComponent(q[1]));
      if (!it) return json(res, 404, { detail: "no such question" });
      Object.assign(it, await readBody(req));
      return json(res, 200, it);
    }
    if (p === "/api/ratings" && m === "POST") {
      const body = await readBody(req);
      db.ratings.push(body);
      return json(res, 201, { id: `ra-${db.ratings.length}`, ...body });
    }
    return json(res, 404, { detail: `no route ${m} ${p}` });
  });

  // ---- WebSocket session ----------------------------------------------------------------------

  const wss = new WebSocketServer({ noServer: true });
  server.on("upgrade", (req, socket, head) => {
    const match = new URL(req.url, "http://x").pathname.match(/^\/ws\/session\/([^/]+)$/);
    if (!match) return socket.destroy();
    wss.handleUpgrade(req, socket, head, (ws) => runSession(ws, decodeURIComponent(match[1])));
  });

  function runSession(ws, sessionId) {
    if (!db.sessions.has(sessionId)) db.sessions.set(sessionId, { frames: [] });
    const rec = db.sessions.get(sessionId);
    let state = "READY";
    let interrupted = null; // {segment_id, offset_ms}
    let answerGen = 0;
    let turn = 0;
    let seq = 0;

    const send = (msg) => {
      if (ws.readyState !== ws.OPEN) return;
      rec.frames.push({ dir: "out", t: Date.now(), msg: msg.type === "tts_chunk" ? { ...msg, data_b64: "<mp3>" } : msg });
      ws.send(JSON.stringify(msg));
    };
    const setState = (s) => {
      if (s === state) return;
      state = s;
      send({ type: "state", state: s });
    };
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

    async function answer(userText) {
      const gen = ++answerGen;
      const turnId = `t${++turn}`;
      send({ type: "transcript", role: "user", text: userText, turn_id: `${turnId}-u`, citations: [] });
      setState("THINKING");
      const a = answerFor(userText);
      await sleep(answerDelayMs);
      if (gen !== answerGen) return;
      setState("SPEAKING_ANSWER");
      let text = "";
      for (const [i, sentence] of a.sentences.entries()) {
        if (gen !== answerGen) return;
        text = text ? `${text} ${sentence}` : sentence;
        const sentenceId = `${turnId}-${i + 1}`;
        send({ type: "transcript", role: "agent", text, turn_id: turnId, citations: a.citations });
        const mp3 = fakeMp3(sentenceId + userText, durationFor(sentence, audioScale), "answer");
        send({ type: "tts_chunk", seq: seq++, format: "mp3", data_b64: mp3.toString("base64"), sentence_id: sentenceId });
        await sleep(120);
      }
      if (gen !== answerGen) return;
      if (a.bookmark) send({ type: "bookmark_created", question_id: `q-${Date.now()}` });
      if (interrupted) {
        send({ type: "resume_brief", segment_id: interrupted.segment_id });
        interrupted = null;
      } else setState("OPEN_FLOOR");
    }

    ws.on("message", (raw) => {
      let m;
      try {
        m = JSON.parse(String(raw));
      } catch {
        return send({ type: "error", code: "bad_json", message: "frame is not JSON" });
      }
      rec.frames.push({ dir: "in", t: Date.now(), msg: m.type === "utterance_audio" ? { ...m, data_b64: `<${m.data_b64?.length}b>` } : m });
      const err = validateClientMessage(m);
      if (err) {
        log("bad client frame:", err, m);
        return send({ type: "error", code: "bad_message", message: err });
      }
      switch (m.type) {
        case "hello":
          send({ type: "state", state });
          break;
        case "playback":
          if (!["LISTENING", "THINKING"].includes(state)) setState("PLAYING_BRIEF");
          break;
        case "interrupt":
          answerGen++; // cancel any answer in flight
          if (m.segment_id) interrupted = { segment_id: m.segment_id, offset_ms: m.offset_ms ?? 0 };
          setState("LISTENING");
          break;
        case "utterance":
          void answer(m.text);
          break;
        case "utterance_audio":
          // Fake server STT: we don't decode audio; reply as if it heard a fixed question.
          void answer("(server STT) who funded that?");
          break;
        case "control":
          if (m.action === "end") {
            answerGen++;
            setState("ENDED");
          } else if (m.action === "resume") {
            answerGen++;
            interrupted = null;
            setState("PLAYING_BRIEF");
          } else {
            answerGen++;
            interrupted = null;
            setState("PLAYING_BRIEF");
          }
          break;
      }
    });
  }

  return new Promise((resolve) => {
    server.listen(port, () => {
      log(`listening on http://localhost:${port} (audio scale ${audioScale})`);
      resolve({
        port,
        close: () =>
          new Promise((r) => {
            for (const c of wss.clients) c.terminate();
            server.close(() => r());
          }),
      });
    });
  });
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  void startMockServer();
}
