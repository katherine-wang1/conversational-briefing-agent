// Where the listener got to in today's brief, per device (localStorage), so a reload or an iOS tab
// eviction resumes at the current sentence instead of the start. Best effort: storage may throw.

const KEY = "briefing.progress";

interface Saved {
  brief_id: string;
  index: number;
}

export function saveProgress(briefId: string, index: number): void {
  try {
    localStorage.setItem(KEY, JSON.stringify({ brief_id: briefId, index } satisfies Saved));
  } catch {
    /* ignore */
  }
}

/** Segment index saved for this brief, or null (other brief, nothing saved, storage unavailable). */
export function loadProgress(briefId: string): number | null {
  try {
    const s = JSON.parse(localStorage.getItem(KEY) ?? "null") as Saved | null;
    return s && s.brief_id === briefId && Number.isInteger(s.index) && s.index >= 0 ? s.index : null;
  } catch {
    return null;
  }
}

export function clearProgress(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}
