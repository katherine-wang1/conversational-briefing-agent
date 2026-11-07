import { describe, expect, it } from "vitest";
import { mergeSources } from "./conversation";

describe("mergeSources", () => {
  it("keeps one chip per article across a streamed answer", () => {
    const a = [{ passage_id: "d1:0-5", source: "Reuters", url: "https://r.com/1" }];
    const b = [
      { passage_id: "d1:9-20", source: "Reuters", url: "https://r.com/1" },
      { passage_id: "x", source: null, url: null },
      { passage_id: "d2:0-1", source: "FT", url: "https://ft.com/2" },
    ];
    expect(mergeSources(a, b).map((s) => s.passage_id)).toEqual(["d1:0-5", "x", "d2:0-1"]);
  });
});
