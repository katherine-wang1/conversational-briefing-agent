import { describe, expect, it } from "vitest";
import { talkView } from "./talkView";

const base = { speechDetected: false, pendingResume: null, interruptedAt: null, segmentIndex: 0 };

describe("talk button states", () => {
  it("shows a clear 'Tap to talk' while the brief plays", () => {
    const v = talkView({ ...base, phase: "playing" }, true, true);
    expect(v).toMatchObject({ mood: "playing", label: "Got a question?", pill: { text: "Tap to talk", kind: "talk" } });
  });

  it("has distinct listening, hearing, thinking, speaking and resuming states", () => {
    expect(talkView({ ...base, phase: "listening" }, true, true).mood).toBe("listening");
    expect(talkView({ ...base, phase: "listening", speechDetected: true }, true, true).mood).toBe("hearing");
    expect(talkView({ ...base, phase: "thinking" }, true, true).mood).toBe("thinking");
    expect(talkView({ ...base, phase: "answering" }, true, true).mood).toBe("speaking");
    expect(talkView({ ...base, phase: "paused", pendingResume: "s3" }, true, true).mood).toBe("resuming");
    // without auto-resume a pause waits for the user
    expect(talkView({ ...base, phase: "paused", pendingResume: "s3" }, false, true).pill?.kind).toBe("play");
  });

  it("offers play / resume before the brief starts", () => {
    expect(talkView({ ...base, phase: "ready" }, true, true).label).toBe("Play today's brief");
    expect(talkView({ ...base, phase: "ready", segmentIndex: 4 }, true, true).label).toBe("Resume today's brief");
    expect(talkView({ ...base, phase: "idle" }, true, false).label).toBe("Loading…");
  });
});
