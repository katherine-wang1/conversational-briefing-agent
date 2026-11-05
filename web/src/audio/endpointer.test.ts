import { describe, expect, it } from "vitest";
import { Endpointer, type EndpointEvent } from "./endpointer";

const opts = { silenceMs: 700, maxUtteranceMs: 30_000, noSpeechTimeoutMs: 8_000, onsetMs: 90 };

function run(ep: Endpointer, pattern: [boolean, number][], frameMs = 20): EndpointEvent[] {
  const out: EndpointEvent[] = [];
  for (const [speech, ms] of pattern) for (let t = 0; t < ms; t += frameMs) out.push(...ep.feed(speech, frameMs));
  return out;
}

describe("Endpointer", () => {
  it("starts after onset and ends after 700 ms of silence", () => {
    const ep = new Endpointer(opts);
    const ev = run(ep, [
      [false, 300],
      [true, 1500],
      [false, 1000],
    ]);
    expect(ev.map((e) => e.type)).toEqual(["speech_start", "end"]);
    const end = ev[1] as Extract<EndpointEvent, { type: "end" }>;
    expect(end.reason).toBe("silence");
    // ended 700 ms after the speech stopped (300 + 1500 + 700)
    expect(end.atMs).toBe(2500);
    expect(end.speechMs).toBe(1500);
  });

  it("does not end on pauses shorter than the silence window", () => {
    const ep = new Endpointer(opts);
    const ev = run(ep, [
      [true, 500],
      [false, 600],
      [true, 500],
      [false, 400],
    ]);
    expect(ev.map((e) => e.type)).toEqual(["speech_start"]);
    expect(ep.ended).toBe(false);
  });

  it("ignores clicks shorter than the onset", () => {
    const ep = new Endpointer({ ...opts, onsetMs: 250 });
    const ev = run(ep, [
      [true, 100],
      [false, 200],
      [true, 100],
      [false, 200],
    ]);
    expect(ev).toEqual([]);
    expect(ep.started).toBe(false);
  });

  it("caps the utterance at maxUtteranceMs", () => {
    const ep = new Endpointer({ ...opts, maxUtteranceMs: 2000 });
    const ev = run(ep, [[true, 3000]]);
    expect(ev.at(-1)).toMatchObject({ type: "end", reason: "max", atMs: 2000 });
  });

  it("gives up when nobody speaks", () => {
    const ep = new Endpointer({ ...opts, noSpeechTimeoutMs: 1000 });
    const ev = run(ep, [[false, 2000]]);
    expect(ev).toEqual([{ type: "end", reason: "no_speech", atMs: 1000, speechMs: 0 }]);
    expect(ep.feed(true, 20)).toEqual([]);
  });

  it("respects a configurable silence window", () => {
    const ep = new Endpointer({ ...opts, silenceMs: 300 });
    const ev = run(ep, [
      [true, 400],
      [false, 400],
    ]);
    expect(ev.at(-1)).toMatchObject({ reason: "silence", atMs: 700 });
  });
});
