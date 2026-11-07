import { describe, expect, it } from "vitest";
import {
  historyFromApi,
  interestsFromApi,
  mePatchToApi,
  questionsFromApi,
  turnsFromApi,
  unwrapList,
} from "./normalize";

describe("normalize (real backend shapes, app/api/routes.py)", () => {
  it("unwraps {key: [...]} and passes arrays through", () => {
    expect(unwrapList([1, 2], "x")).toEqual([1, 2]);
    expect(unwrapList({ x: [3] }, "x")).toEqual([3]);
    expect(unwrapList({ y: [3] }, "x")).toEqual([]);
    expect(unwrapList(null, "x")).toEqual([]);
  });

  it("interests: {interests: [...]}", () => {
    const it0 = { id: "i1", label: "batteries", weight: 0.4 };
    expect(interestsFromApi({ interests: [it0] })).toEqual([it0]);
  });

  it("questions: {questions: [...]} with the follow-up result as answer", () => {
    const qs = questionsFromApi({
      questions: [
        {
          id: "q1",
          question: "who funded it?",
          status: "answered",
          created_at: "2026-11-06T00:00:00Z",
          answer: { answer_sentences: [{ text: "A.", cites: ["p1"] }, { text: "B.", cites: [] }] },
        },
        { id: "q2", question: "x", status: "open", created_at: "2026-11-06T00:00:00Z", answer: null },
      ],
    });
    expect(qs[0].answer).toEqual({ text: "A. B." });
    expect(qs[1].answer).toBeNull();
  });

  it("history: briefs + sessions -> one item per brief with its latest session", () => {
    const items = historyFromApi({
      briefs: [
        { id: "br2", local_date: "2026-11-06", status: "ready", stories: [{ headline: "H1" }, { headline: "H2" }] },
        { id: "br1", local_date: "2026-11-05", status: "ready", stories: null },
      ],
      sessions: [
        { id: "se3", brief_id: "br2", started_at: "2026-11-06T10:00:00Z" },
        { id: "se2", brief_id: "br2", started_at: "2026-11-06T09:00:00Z" },
      ],
    });
    expect(items).toEqual([
      { brief_id: "br2", local_date: "2026-11-06", headlines: ["H1", "H2"], session_id: "se3" },
      { brief_id: "br1", local_date: "2026-11-05", headlines: [], session_id: null },
    ]);
    const already = [{ brief_id: "b", local_date: "d", headlines: [] }];
    expect(historyFromApi(already)).toBe(already);
  });

  it("transcript: drops system and empty turns", () => {
    expect(
      turnsFromApi({
        turns: [
          { role: "system", text: "state" },
          { role: "user", text: "why?" },
          { role: "agent", text: "Because.", citations: ["p1"] },
          { role: "agent", text: "" },
        ],
      }),
    ).toEqual([
      { role: "user", text: "why?", citations: [] },
      { role: "agent", text: "Because.", citations: ["p1"] },
    ]);
  });

  it("PATCH /api/me body: profile fields flat, client settings under settings", () => {
    expect(mePatchToApi({ stt_mode: "server" })).toEqual({ settings: { stt_mode: "server" } });
    expect(mePatchToApi({ brief_time: "07:30", research_consent: true, headphones_mode: true })).toEqual({
      brief_time: "07:30",
      research_consent: true,
      settings: { headphones_mode: true },
    });
    expect(mePatchToApi({ depth_pref: "deep" })).toEqual({ delivery: { depth: "deep" } });
  });
});
