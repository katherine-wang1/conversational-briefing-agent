// Adapters from the real backend's REST shapes (app/api/routes.py) to the client types in
// contracts/api.ts. Each accepts the client shape too (the mock server, older backends), so they
// are safe to apply unconditionally.

import type { HistoryItem, HistoryTurn, OpenQuestion, Settings } from "../contracts/api";
import type { Interest } from "../contracts/brief";

type Obj = Record<string, unknown>;
const isObj = (x: unknown): x is Obj => typeof x === "object" && x !== null && !Array.isArray(x);

/** `[...]` or `{<key>: [...]}` → the list. */
export function unwrapList<T>(raw: unknown, key: string): T[] {
  if (Array.isArray(raw)) return raw as T[];
  if (isObj(raw) && Array.isArray(raw[key])) return raw[key] as T[];
  return [];
}

export const interestsFromApi = (raw: unknown): Interest[] => unwrapList<Interest>(raw, "interests");

/** Backend rows keep the follow-up result in `answer` ({answer_sentences: [{text, cites}], ...}). */
export function questionFromApi(q: Obj): OpenQuestion {
  const a = q.answer;
  let answer: OpenQuestion["answer"] = null;
  if (isObj(a)) {
    if (typeof a.text === "string") answer = { text: a.text };
    else if (Array.isArray(a.answer_sentences)) {
      const text = a.answer_sentences
        .map((s) => (isObj(s) && typeof s.text === "string" ? s.text : ""))
        .filter(Boolean)
        .join(" ");
      answer = text ? { text } : typeof a.what_was_found === "string" && a.what_was_found ? { text: a.what_was_found } : null;
    } else if (typeof a.what_was_found === "string" && a.what_was_found) answer = { text: a.what_was_found };
  }
  return { ...(q as unknown as OpenQuestion), answer };
}

export const questionsFromApi = (raw: unknown): OpenQuestion[] =>
  unwrapList<Obj>(raw, "questions").map(questionFromApi);

/** Backend: `{briefs: [{id, local_date, stories: [{headline}]}], sessions: [{id, brief_id, started_at}]}`
 * (both newest first). Client: one item per brief, with its latest session. */
export function historyFromApi(raw: unknown): HistoryItem[] {
  if (Array.isArray(raw)) return raw as HistoryItem[];
  if (!isObj(raw)) return [];
  const briefs = unwrapList<Obj>(raw, "briefs");
  const sessions = unwrapList<Obj>(raw, "sessions");
  return briefs.map((b) => {
    const stories = Array.isArray(b.stories) ? b.stories : [];
    const latest = sessions.find((s) => s.brief_id === b.id);
    return {
      brief_id: String(b.id),
      local_date: String(b.local_date),
      headlines: stories.map((s) => (isObj(s) ? String(s.headline ?? "") : "")).filter(Boolean),
      session_id: latest ? String(latest.id) : null,
    };
  });
}

/** GET /api/sessions/{id}/transcript → `{turns: [{role, text, citations}]}`; system turns hidden. */
export function turnsFromApi(raw: unknown): HistoryTurn[] {
  return unwrapList<Obj>(raw, "turns")
    .filter((t) => t.role === "user" || t.role === "agent")
    .filter((t) => typeof t.text === "string" && t.text.trim() !== "")
    .map((t) => ({
      role: t.role as HistoryTurn["role"],
      text: String(t.text),
      citations: Array.isArray(t.citations) ? t.citations.map(String) : [],
    }));
}

const PROFILE_KEYS = ["brief_time", "timezone", "research_consent", "display_name"] as const;
const SETTINGS_KEYS = ["voice", "stt_mode", "auto_resume", "headphones_mode"] as const;

/** Client settings patch → the backend's PATCH /api/me body (profile fields flat, the rest under
 * `settings`, depth under `delivery`). Unknown keys are dropped (the backend would reject them). */
export function mePatchToApi(patch: Partial<Settings> & { research_consent?: boolean }): Obj {
  const body: Obj = {};
  const settings: Obj = {};
  for (const [k, v] of Object.entries(patch)) {
    if (v === undefined) continue;
    if ((PROFILE_KEYS as readonly string[]).includes(k)) body[k] = v;
    else if ((SETTINGS_KEYS as readonly string[]).includes(k)) settings[k] = v;
    else if (k === "depth_pref") body.delivery = { depth: v };
  }
  if (Object.keys(settings).length) body.settings = settings;
  return body;
}
