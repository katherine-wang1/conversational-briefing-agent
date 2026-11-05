import { describe, expect, it } from "vitest";
import { EnergyGate } from "./vad";

const gate = () => new EnergyGate({ ratio: 3, absFloor: 0.012 }, 75);

describe("EnergyGate", () => {
  it("separates speech from a quiet room", () => {
    const g = gate();
    for (let i = 0; i < 100; i++) expect(g.decide(0.004)).toBe(false);
    expect(g.decide(0.1)).toBe(true);
  });

  it("stops treating a steady loud room as speech within the window", () => {
    const g = gate();
    let speech = 0;
    for (let i = 0; i < 200; i++) if (g.decide(0.02)) speech++;
    expect(g.noise).toBeCloseTo(0.02);
    expect(g.decide(0.02)).toBe(false);
    expect(speech).toBe(0); // first frame sets the floor
    expect(g.decide(0.2)).toBe(true);
  });

  it("keeps a long utterance (with syllable dips) as speech", () => {
    const g = gate();
    for (let i = 0; i < 50; i++) g.decide(0.004);
    let lostLoud = 0;
    for (let i = 0; i < 1500; i++) {
      const dip = i % 12 >= 10;
      const isSpeech = g.decide(dip ? 0.006 : 0.08);
      if (!dip && !isSpeech) lostLoud++;
    }
    expect(lostLoud).toBe(0);
  });
});
