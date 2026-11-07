import { describe, expect, it } from "vitest";
import { DebugLog, LOG_CAP, summarizeFrame } from "./debugLog";

describe("DebugLog (live log)", () => {
  it("records nothing while hidden", () => {
    const log = new DebugLog();
    log.add("info", "x");
    expect(log.entries.get()).toEqual([]);
  });

  it("keeps the newest entries, capped, oldest first", () => {
    const log = new DebugLog();
    log.enabled = true;
    for (let i = 0; i < LOG_CAP + 25; i++) log.add("audio", `e${i}`);
    const xs = log.entries.get();
    expect(xs).toHaveLength(LOG_CAP);
    expect(xs[0].text).toBe("e25");
    expect(xs.at(-1)!.text).toBe(`e${LOG_CAP + 24}`);
    log.clear();
    expect(log.entries.get()).toEqual([]);
  });

  it("maps server events, unknown kinds to info, and uses the server timestamp", () => {
    const log = new DebugLog();
    log.enabled = true;
    log.server("model", "voice.turn: 812 ms", 1_700_000_000.5);
    log.server("weird", "?", 1_700_000_001);
    const [a, b] = log.entries.get();
    expect(a).toMatchObject({ kind: "model", from: "server", t: 1_700_000_000_500 });
    expect(b.kind).toBe("info");
  });

  it("summarises frames without payloads", () => {
    const tts = summarizeFrame({ type: "tts_chunk", seq: 3, format: "wav", data_b64: "A".repeat(8192), sentence_id: "t-1" });
    expect(tts).toBe("tts_chunk #3 wav 6 KB");
    expect(summarizeFrame({ type: "utterance_audio", format: "webm/opus", data_b64: "A".repeat(4096) })).toMatch(/KB$/);
    expect(summarizeFrame({ type: "interrupt", segment_id: null, offset_ms: 0 })).toBe("interrupt (answer) +0ms");
    const t = summarizeFrame({
      type: "transcript",
      role: "agent",
      text: "Hi.",
      citations: ["a"],
      sources: [{ passage_id: "a" }],
    });
    expect(t).toBe('transcript agent "Hi." (1 source)');
  });
});
