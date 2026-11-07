import { describe, expect, it } from "vitest";
import { BriefPlayer, type Position } from "./briefPlayer";
import type { AudioOutput } from "./output";

describe("BriefPlayer", () => {
  it("reports each finished segment as completed, so the server knows the brief ended", async () => {
    const results = ["ended", "error", "ended"] as const;
    let n = 0;
    const out = {
      owner: "brief",
      play: async () => results[n++],
      stop: () => {},
      positionMs: () => 0,
    } as unknown as AudioOutput;
    const segs = ["s0", "s1", "s2"].map((sid) => ({ sid, url: `/media/${sid}.wav`, duration_ms: 1234.5 }));
    const pos: Position[] = [];
    const ended = new Promise<void>((resolve) => {
      const p = new BriefPlayer(out, segs, { onPosition: (x) => pos.push(x), onEnded: resolve }, (u) => u, 60_000);
      p.preload = () => Promise.resolve(null);
      p.playFrom(0);
    });
    await ended;
    expect(pos.filter((x) => x.completed)).toEqual([
      { segment_id: "s0", offset_ms: 1235, completed: true },
      { segment_id: "s2", offset_ms: 1235, completed: true },
    ]);
  });
});
