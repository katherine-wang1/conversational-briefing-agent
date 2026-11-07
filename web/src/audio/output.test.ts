import { describe, expect, it } from "vitest";
import { AudioOutput, clampRate } from "./output";

function fakeEl() {
  const el = document.createElement("audio");
  el.play = () => Promise.resolve();
  el.pause = () => {};
  return el;
}

describe("AudioOutput playback speed (TODO 1.21)", () => {
  it("applies the rate with pitch preserved, and re-applies it after every src change / load", async () => {
    const el = fakeEl();
    const out = new AudioOutput(el);
    const events: string[] = [];
    out.onEvent = (_e, t) => events.push(t);
    out.setRate(1.2);
    expect(el.playbackRate).toBe(1.2);
    expect(el.defaultPlaybackRate).toBe(1.2);
    expect(el.preservesPitch).toBe(true);
    expect((el as unknown as { webkitPreservesPitch: boolean }).webkitPreservesPitch).toBe(true);

    void out.play("blob:seg1", 0, "brief", "s001");
    el.playbackRate = 1; // what browsers do when a new source loads
    el.dispatchEvent(new Event("loadedmetadata"));
    expect(el.playbackRate).toBe(1.2);
    el.playbackRate = 1;
    el.dispatchEvent(new Event("playing"));
    expect(el.playbackRate).toBe(1.2);
    expect(events.some((t) => t.includes("s001 playing at 1.2×"))).toBe(true);

    void out.play("blob:answer", 0, "answer", "t1-1");
    expect(el.playbackRate).toBe(1.2); // set before play(), not only on events
  });

  it("clamps silly rates", () => {
    expect(clampRate(9)).toBe(2);
    expect(clampRate(0.1)).toBe(0.8);
    expect(clampRate("x")).toBe(1.2);
  });
});
