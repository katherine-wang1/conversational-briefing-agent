// Canned brief, answers and memory for the mock server. Companies and products are fictional
// (taken from the PRD's illustrative example session, §5.3).

export const BRIEF_ID = "brief-mock-1";

const S = (sid, text, story_ref, kind = "story", cites = []) => ({ sid, text, story_ref, kind, cites });

export function briefScript(today = new Date()) {
  const local_date = today.toISOString().slice(0, 10);
  const stories = [
    {
      story_ref: "st-atlas",
      slot: "big_news",
      headline: "Northwind AI released Atlas-3, an open-weights reasoning model",
      why_tag: "big news",
      aliases: ["atlas", "northwind", "open weights"],
    },
    {
      story_ref: "st-longctx",
      slot: "for_you",
      headline: "Stanford/CMU paper: long-context retrieval falls by a third past 200k tokens",
      why_tag: "for you",
      aliases: ["long context", "paper", "retrieval"],
    },
    {
      story_ref: "st-voltaic",
      slot: "for_you",
      headline: "Voltaic Storage signs a 2 GWh sodium-ion deal with Lonestar Power",
      why_tag: "for you",
      aliases: ["battery", "voltaic", "sodium"],
    },
    {
      story_ref: "st-kinema",
      slot: "outside_usual",
      headline: "Kinema releases an open robot foundation model trained mostly in simulation",
      why_tag: "outside your usual",
      aliases: ["robot", "kinema", "simulation"],
    },
  ];
  const sentences = [
    S("s1", "Morning. Four stories today, about two minutes.", null, "intro"),
    S("s2", "First, Northwind AI released Atlas-3, an open-weights reasoning model.", "st-atlas", "story", ["p:atl-1"]),
    S("s3", "It scores within two points of the top closed models on the main math and coding benchmarks.", "st-atlas", "story", ["p:atl-2"]),
    S("s4", "Next, because you follow agent memory:", null, "transition"),
    S("s5", "A Stanford and CMU paper finds long-context retrieval accuracy falls by about a third past 200k tokens.", "st-longctx", "story", ["p:lc-1"]),
    S("s6", "The drop held across all five models they tested.", "st-longctx", "story", ["p:lc-2"]),
    S("s7", "In energy, Voltaic Storage signed a two gigawatt-hour grid battery deal with Lonestar Power.", "st-voltaic", "story", ["p:vs-1"]),
    S("s8", "It is the largest sodium-ion order so far.", "st-voltaic", "story", ["p:vs-2"]),
    S("s9", "Outside your usual: Kinema released an open robot foundation model trained mostly in simulation.", "st-kinema", "story", ["p:kn-1"]),
    S("s10", "Kinema says it transfers to real warehouse arms with no fine-tuning.", "st-kinema", "story", ["p:kn-2"]),
    S("s11", "That's the brief. What do you want to dig into?", null, "outro"),
  ];
  return {
    brief_id: BRIEF_ID,
    user_id: "user-mock",
    local_date,
    arm: "full",
    stories,
    sentences,
    est_duration_s: 120,
  };
}

/** Canned answers keyed by a keyword test on the user's utterance. */
export function answerFor(text) {
  const t = text.toLowerCase();
  if (/(look into|find out|bookmark|tomorrow|atlas-3 too)/.test(t))
    return {
      sentences: [
        "The paper didn't test that, and I couldn't find anyone who has yet.",
        "I'll look into it and tell you tomorrow.",
      ],
      citations: [],
      bookmark: true,
    };
  if (/(fund|money|raise|invest)/.test(t))
    return {
      sentences: [
        "Northwind's last round was led by Halden Ventures, according to its blog.",
        "The Atlas-3 release itself didn't mention new funding.",
      ],
      citations: ["p:atl-3"],
    };
  if (/(which model|held up|best)/.test(t))
    return {
      sentences: [
        "Of the five models, the two with retrieval-augmented attention held up best.",
        "They lost about fifteen percent, versus a third for the others.",
      ],
      citations: ["p:lc-3"],
    };
  if (/(source|where)/.test(t))
    return { sentences: ["That's from the company's announcement and an arXiv paper."], citations: ["p:atl-1"] };
  return {
    sentences: ["Good question.", "Based on today's sources, here is the short version: it's early, but the numbers look solid."],
    citations: ["p:atl-2"],
  };
}

export function initialInterests() {
  const now = new Date().toISOString();
  return [
    { id: "i1", label: "agent memory and long context", subtopic: "llm-research", kind: "topic", weight: 1.2, confidence: 0.6, explicit_pin: true, source: "onboarding", last_reinforced_at: now },
    { id: "i2", label: "grid-scale batteries", subtopic: "storage", kind: "topic", weight: 0.9, confidence: 0.5, explicit_pin: false, source: "onboarding", last_reinforced_at: now },
    { id: "i3", label: "Northwind AI", subtopic: "frontier-labs", entity: "Northwind AI", kind: "entity", weight: 0.7, confidence: 0.4, explicit_pin: false, source: "implicit", last_reinforced_at: now },
  ];
}

export function initialQuestions() {
  return [
    {
      id: "q1",
      question: "Why did Halden's chip launch slip?",
      status: "answered",
      answer: { text: "Halden said a packaging supplier missed its qualification date; the launch moved one quarter." },
      citations: ["p:hd-1"],
      created_at: new Date(Date.now() - 86400e3).toISOString(),
      answered_at: new Date().toISOString(),
    },
  ];
}

/** GET /api/history in the real backend's shape (app/api/routes.py): briefs + sessions, newest
 * first. Turns come from GET /api/sessions/{id}/transcript (see `transcript`). */
export function history() {
  const d = new Date(Date.now() - 86400e3).toISOString().slice(0, 10);
  return {
    briefs: [
      {
        id: "brief-mock-0",
        local_date: d,
        status: "ready",
        arm: "full",
        stories: [
          { story_ref: "d:hd", headline: "Halden delays its inference chip" },
          { story_ref: "d:eu", headline: "EU AI Office drafts training-data disclosure guidance" },
        ],
      },
    ],
    sessions: [
      { id: "sess-old", brief_id: "brief-mock-0", started_at: `${d}T15:00:00Z`, ended_at: null, end_reason: null, n_turns: 3 },
    ],
  };
}

export function transcript(sessionId) {
  if (sessionId !== "sess-old") return null;
  return {
    turns: [
      { seq: 0, role: "system", text: "", action: "state" },
      { seq: 1, role: "agent", text: "Halden pushed its inference chip launch by a quarter.", citations: ["p:hd-0"] },
      { seq: 2, role: "user", text: "Why did it slip?", citations: [] },
      { seq: 3, role: "agent", text: "They didn't say. I'll look into it and tell you tomorrow.", citations: [] },
    ],
  };
}
