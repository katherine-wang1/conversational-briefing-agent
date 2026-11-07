import { Store } from "./store";

/** Client-side audio/voice settings. The user-facing ones are also PATCHed to /api/me. */
export interface ClientSettings {
  stt_mode: "browser" | "server";
  auto_resume: boolean;
  headphones_mode: boolean;
  voice: string;
  brief_time: string;
  /** VAD engine: Silero (vad-web, default) or a simple energy detector (fallback / tests). */
  vad_engine: "silero" | "energy";
  /** End of speech after this much silence. */
  silence_ms: number;
  /** Hard cap on one utterance. */
  max_utterance_ms: number;
  /** Close the mic if no speech starts within this time. */
  no_speech_timeout_ms: number;
  /** Stage 2: voiced audio needed to count as barge-in. */
  barge_in_onset_ms: number;
  /** Which tap event interrupts: pointerdown (fastest) or click (if iOS needs a full gesture). */
  tap_event: "pointerdown" | "click";
  /** Playback speed for the brief and spoken answers (pitch preserved). Synced to the server. */
  playback_rate: number;
  /** Live log ("Show what's happening"). Synced to the server. */
  show_debug: boolean;
}

export const DEFAULT_SETTINGS: ClientSettings = {
  stt_mode: "browser",
  auto_resume: true,
  headphones_mode: false,
  voice: "default",
  brief_time: "07:30",
  vad_engine: "silero",
  silence_ms: 700,
  max_utterance_ms: 30_000,
  no_speech_timeout_ms: 8_000,
  barge_in_onset_ms: 250,
  tap_event: "pointerdown",
  playback_rate: 1.2,
  show_debug: false,
};

const KEY = "briefing.settings";

function readStored(): Partial<ClientSettings> {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? "{}");
  } catch {
    return {};
  }
}

/** URL overrides for testing on a device, e.g. ?vad=energy&stt=server&silence=500&headphones=1&rate=1.5&debug=1.
 * They win over stored and server settings and are not persisted. */
function readOverrides(): Partial<ClientSettings> {
  const o: Partial<ClientSettings> = {};
  try {
    const q = new URLSearchParams(window.location.search);
    const vad = q.get("vad");
    if (vad === "energy" || vad === "silero") o.vad_engine = vad;
    const stt = q.get("stt");
    if (stt === "browser" || stt === "server") o.stt_mode = stt;
    const silence = Number(q.get("silence"));
    if (silence > 0) o.silence_ms = silence;
    if (q.get("headphones") === "1") o.headphones_mode = true;
    const rate = Number(q.get("rate"));
    if (rate >= 0.8 && rate <= 2) o.playback_rate = rate;
    if (q.get("debug") === "1") o.show_debug = true;
  } catch {
    /* ignore */
  }
  return o;
}

const overrides = readOverrides();
let stored = readStored();

export const settings = new Store<ClientSettings>({ ...DEFAULT_SETTINGS, ...stored, ...overrides });

/** Apply settings from GET /api/me without clobbering URL overrides. */
export function applyServerSettings(p: Partial<ClientSettings>): void {
  const clean = Object.fromEntries(
    Object.entries(p).filter(([k, v]) => v !== undefined && v !== null && !(k in overrides)),
  ) as Partial<ClientSettings>;
  settings.patch(clean);
}

settings.subscribe(() => {
  const cur = settings.get();
  const toStore: Partial<ClientSettings> = { ...stored };
  for (const k of Object.keys(cur) as (keyof ClientSettings)[]) {
    if (k in overrides && cur[k] === overrides[k]) continue;
    (toStore as Record<string, unknown>)[k] = cur[k];
  }
  stored = toStore;
  try {
    localStorage.setItem(KEY, JSON.stringify(toStore));
  } catch {
    /* private mode */
  }
});
