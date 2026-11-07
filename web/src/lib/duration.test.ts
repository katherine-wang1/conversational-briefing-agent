import { describe, expect, it } from "vitest";
import { briefSeconds, formatDuration } from "./duration";

const manifest = {
  brief_id: "b",
  segments: [6000, 9000, 15000, 120000].map((d, i) => ({ sid: `s${i}`, url: "", duration_ms: d })),
};

describe("brief duration (TODO 1.25 / 1.21)", () => {
  it("sums the manifest's segment durations and divides by the speed", () => {
    expect(briefSeconds(manifest, null, 1)).toBe(150);
    expect(briefSeconds(manifest, null, 1.2)).toBeCloseTo(125);
    expect(briefSeconds(manifest, null, 1, 3)).toBe(120); // remaining from segment 3
  });

  it("falls back to the script estimate without a manifest", () => {
    const script = { est_duration_s: 120 } as never;
    expect(briefSeconds(null, script, 1.5)).toBe(80);
    expect(briefSeconds(null, null, 1)).toBeNull();
  });

  it("formats minutes for the header", () => {
    expect(formatDuration(150)).toBe("~2.5 min");
    expect(formatDuration(125)).toBe("~2 min");
    expect(formatDuration(30)).toBe("<1 min");
    expect(formatDuration(13 * 60 + 20)).toBe("~13 min");
    expect(formatDuration(null)).toBe("");
  });
});
