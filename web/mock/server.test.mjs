// @vitest-environment node
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import WebSocket from "ws";
import { startMockServer, validateClientMessage } from "./server.mjs";

describe("validateClientMessage", () => {
  it("accepts contract-shaped frames", () => {
    expect(validateClientMessage({ type: "hello", session_id: "s", client: { stt: "browser" } })).toBeNull();
    expect(validateClientMessage({ type: "playback", segment_id: "s1", offset_ms: 1400 })).toBeNull();
    expect(validateClientMessage({ type: "interrupt", segment_id: null, offset_ms: 0 })).toBeNull();
    expect(validateClientMessage({ type: "control", action: "resume" })).toBeNull();
    expect(validateClientMessage({ type: "debug_subscribe", enabled: true })).toBeNull();
  });

  it("rejects unknown fields, missing fields and bad values", () => {
    expect(validateClientMessage({ type: "playback", segment_id: "s1", offset_ms: 1, extra: 1 })).toMatch(/unknown/);
    expect(validateClientMessage({ type: "utterance" })).toMatch(/missing/);
    expect(validateClientMessage({ type: "control", action: "rewind" })).toMatch(/bad action/);
    expect(validateClientMessage({ type: "playback", segment_id: "s1", offset_ms: 1.5 })).toMatch(/int/);
  });
});

describe("mock session server", () => {
  let srv;
  const port = 8811;
  beforeAll(async () => {
    srv = await startMockServer({ port, audioScale: 0.1, answerDelayMs: 10, quiet: true });
  });
  afterAll(() => srv.close());

  it("serves today's brief with a manifest and fake mp3 audio", async () => {
    const headers = { Authorization: "Bearer dev" };
    const today = await (await fetch(`http://localhost:${port}/api/briefs/today`, { headers })).json();
    expect(today.status).toBe("ready");
    expect(today.manifest.segments.length).toBe(today.script.sentences.length);
    const audio = await fetch(`http://localhost:${port}${today.manifest.segments[0].url}`);
    expect(audio.headers.get("content-type")).toBe("audio/mpeg");
    const bytes = new Uint8Array(await audio.arrayBuffer());
    expect(bytes.length).toBeGreaterThan(100);
  });

  it("requires a bearer token on /api", async () => {
    expect((await fetch(`http://localhost:${port}/api/me`)).status).toBe(401);
  });

  it("runs interrupt -> utterance -> answer -> resume_brief", async () => {
    const ws = new WebSocket(`ws://localhost:${port}/ws/session/test-1?token=dev`);
    const got = [];
    await new Promise((r) => ws.on("open", r));
    const done = new Promise((resolve) =>
      ws.on("message", (raw) => {
        const m = JSON.parse(String(raw));
        got.push(m);
        if (m.type === "resume_brief") resolve();
      }),
    );
    const send = (m) => ws.send(JSON.stringify(m));
    send({ type: "hello", session_id: "test-1", client: { stt: "browser", tts: "server" } });
    send({ type: "control", action: "play" });
    send({ type: "playback", segment_id: "s2", offset_ms: 900 });
    send({ type: "interrupt", segment_id: "s2", offset_ms: 1200 });
    send({ type: "utterance", text: "who funded that?", stt: "browser", end_of_speech_ts: Date.now() / 1000 });
    await done;
    ws.close();
    const states = got.filter((m) => m.type === "state").map((m) => m.state);
    expect(states).toEqual(["READY", "PLAYING_BRIEF", "LISTENING", "THINKING", "SPEAKING_ANSWER"]);
    const chunks = got.filter((m) => m.type === "tts_chunk");
    expect(chunks.length).toBe(2);
    expect(chunks.map((c) => c.seq)).toEqual([0, 1]);
    expect(got.at(-1)).toEqual({ type: "resume_brief", segment_id: "s2" });
    expect(got.some((m) => m.type === "error")).toBe(false);
    expect(got.some((m) => m.type === "debug")).toBe(false); // not subscribed
    const answer = got.find((m) => m.type === "transcript" && m.role === "agent");
    expect(answer.sources[0]).toMatchObject({ source: "Northwind blog", url: expect.stringMatching(/^https:/) });
  });

  it("sends debug events after debug_subscribe", async () => {
    const ws = new WebSocket(`ws://localhost:${port}/ws/session/test-3?token=dev`);
    const got = [];
    await new Promise((r) => ws.on("open", r));
    const done = new Promise((resolve) =>
      ws.on("message", (raw) => {
        const m = JSON.parse(String(raw));
        got.push(m);
        if (m.type === "state" && m.state === "OPEN_FLOOR") resolve();
      }),
    );
    const send = (m) => ws.send(JSON.stringify(m));
    send({ type: "hello", session_id: "test-3", client: {} });
    send({ type: "debug_subscribe", enabled: true });
    send({ type: "utterance", text: "anything new?", stt: "browser" });
    await done;
    ws.close();
    const kinds = got.filter((m) => m.type === "debug").map((m) => m.kind);
    expect(kinds).toEqual(expect.arrayContaining(["info", "heard", "state", "model", "speak"]));
  });

  it("answers a bad frame with an error", async () => {
    const ws = new WebSocket(`ws://localhost:${port}/ws/session/test-2`);
    await new Promise((r) => ws.on("open", r));
    const msg = new Promise((r) => ws.once("message", (raw) => r(JSON.parse(String(raw)))));
    ws.send(JSON.stringify({ type: "playback", segment_id: "s1", offset_ms: 1, bogus: true }));
    expect(await msg).toMatchObject({ type: "error", code: "bad_message" });
    ws.close();
  });
});
