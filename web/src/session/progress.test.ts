import { beforeEach, describe, expect, it } from "vitest";
import { clearProgress, loadProgress, saveProgress } from "./progress";

describe("brief progress (per device)", () => {
  beforeEach(() => localStorage.clear());

  it("remembers the segment index for the same brief only", () => {
    expect(loadProgress("br_1")).toBeNull();
    saveProgress("br_1", 7);
    expect(loadProgress("br_1")).toBe(7);
    expect(loadProgress("br_2")).toBeNull(); // tomorrow's brief starts from the top
    clearProgress();
    expect(loadProgress("br_1")).toBeNull();
  });

  it("ignores junk in storage", () => {
    localStorage.setItem("briefing.progress", "{not json");
    expect(loadProgress("br_1")).toBeNull();
    localStorage.setItem("briefing.progress", JSON.stringify({ brief_id: "br_1", index: -1 }));
    expect(loadProgress("br_1")).toBeNull();
  });
});
