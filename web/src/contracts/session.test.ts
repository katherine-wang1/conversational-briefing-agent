import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { CLIENT_FIELDS, parseServerMessage, SERVER_FIELDS, SESSION_STATES } from "./session";

// Parse app/contracts/session.py (the source of truth) and check the TS mirror against it.
const PY = path.resolve(__dirname, "../../../app/contracts/session.py");
const src = fs.readFileSync(PY, "utf8");

function pyClasses(): Record<string, { type: string; fields: string[] }> {
  const out: Record<string, { type: string; fields: string[] }> = {};
  const re = /^class (\w+)\(Strict\):\n((?: {4}.*\n|\n)+)/gm;
  for (const m of src.matchAll(re)) {
    const body = m[2];
    const fields = [...body.matchAll(/^ {4}(\w+):/gm)].map((f) => f[1]);
    const type = body.match(/type: Literal\["(\w+)"\]/)?.[1];
    if (type) out[type] = { type, fields };
  }
  return out;
}

describe("session contract mirror", () => {
  const classes = pyClasses();

  it("finds the python message classes", () => {
    expect(Object.keys(classes).length).toBe(12);
  });

  it("client message fields match app/contracts/session.py", () => {
    for (const [type, fields] of Object.entries(CLIENT_FIELDS)) {
      expect(classes[type]?.fields, type).toEqual([...fields]);
    }
  });

  it("server message fields match app/contracts/session.py", () => {
    for (const [type, fields] of Object.entries(SERVER_FIELDS)) {
      expect(classes[type]?.fields, type).toEqual([...fields]);
    }
  });

  it("every python message type is mirrored", () => {
    const mirrored = new Set([...Object.keys(CLIENT_FIELDS), ...Object.keys(SERVER_FIELDS)]);
    expect(new Set(Object.keys(classes))).toEqual(mirrored);
  });

  it("session states match", () => {
    const lit = src.match(/SessionState = Literal\[([\s\S]*?)\]/)![1];
    const states = [...lit.matchAll(/"(\w+)"/g)].map((m) => m[1]);
    expect(states).toEqual([...SESSION_STATES]);
  });

  it("control actions match", () => {
    const lit = src.match(/action: Literal\[([^\]]*)\]/)![1];
    const actions = [...lit.matchAll(/"(\w+)"/g)].map((m) => m[1]);
    expect(actions.sort()).toEqual(["end", "next", "play", "resume", "skip"]);
  });
});

describe("parseServerMessage", () => {
  it("parses known frames", () => {
    expect(parseServerMessage('{"type":"state","state":"THINKING"}')).toEqual({ type: "state", state: "THINKING" });
    expect(parseServerMessage('{"type":"resume_brief","segment_id":"s12"}')).toEqual({
      type: "resume_brief",
      segment_id: "s12",
    });
    const t = parseServerMessage('{"type":"transcript","role":"agent","text":"hi","turn_id":"t1"}');
    expect(t).toMatchObject({ role: "agent", citations: [] });
  });

  it("rejects malformed and unknown frames without throwing", () => {
    expect(parseServerMessage("not json")).toBeNull();
    expect(parseServerMessage('{"type":"state","state":"NOPE"}')).toBeNull();
    expect(parseServerMessage('{"type":"future_thing"}')).toBeNull();
    expect(parseServerMessage('{"type":"tts_chunk","seq":"x"}')).toBeNull();
  });
});
