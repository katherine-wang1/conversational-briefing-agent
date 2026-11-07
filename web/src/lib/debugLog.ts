import { Store } from "./store";

/** Live log ("Show what's happening"): client and server events, newest last, capped. */
export type LogKind =
  | "ws" // socket state
  | "out" // client -> server frame
  | "in" // server -> client frame
  | "audio" // segment load / play / end / error, rate
  | "latency" // interrupt -> pause, end of speech -> first audio
  | "stt" // speech recognition start / partial / final
  | "state" // server state transitions
  | "heard"
  | "model"
  | "tool"
  | "speak"
  | "tts"
  | "drop"
  | "memory"
  | "error"
  | "info";

export interface LogEntry {
  id: number;
  t: number; // epoch ms
  kind: LogKind;
  from: "client" | "server";
  text: string;
}

export const LOG_CAP = 500;

const KINDS = new Set<string>([
  "ws",
  "out",
  "in",
  "audio",
  "latency",
  "stt",
  "state",
  "heard",
  "model",
  "tool",
  "speak",
  "tts",
  "drop",
  "memory",
  "error",
  "info",
]);

export class DebugLog {
  readonly entries = new Store<LogEntry[]>([]);
  /** Off unless the live log is shown (no cost when hidden). */
  enabled = false;
  private n = 0;

  add(kind: LogKind, text: string, from: "client" | "server" = "client", t = Date.now()): void {
    if (!this.enabled) return;
    const e: LogEntry = { id: ++this.n, t, kind, from, text: text.length > 600 ? `${text.slice(0, 600)}…` : text };
    this.entries.set((xs) => {
      const next = xs.length >= LOG_CAP ? xs.slice(xs.length - LOG_CAP + 1) : xs.slice();
      next.push(e);
      return next;
    });
  }

  server(kind: string, text: string, tsSeconds: number): void {
    this.add((KINDS.has(kind) ? kind : "info") as LogKind, text, "server", Math.round(tsSeconds * 1000));
  }

  clear(): void {
    this.entries.set([]);
  }
}

export const debugLog = new DebugLog();

/** One-line summary of a protocol frame (no audio payloads, no tokens). */
export function summarizeFrame(msg: Record<string, unknown>): string {
  const t = String(msg.type);
  switch (t) {
    case "hello":
      return `hello ${JSON.stringify(msg.client ?? {})}`;
    case "playback":
      return `playback ${msg.segment_id} +${msg.offset_ms}ms${msg.completed ? " completed" : ""}`;
    case "interrupt":
      return `interrupt ${msg.segment_id ?? "(answer)"} +${msg.offset_ms ?? 0}ms`;
    case "utterance":
      return `utterance "${clip(String(msg.text), 120)}" (${msg.stt ?? "browser"})`;
    case "utterance_audio":
      return `utterance_audio ${msg.format} ${typeof msg.data_b64 === "string" ? Math.round((msg.data_b64.length * 3) / 4096) : "?"} KB`;
    case "control":
      return `control ${msg.action}`;
    case "debug_subscribe":
      return `debug_subscribe ${msg.enabled === false ? "off" : "on"}`;
    case "state":
      return `state ${msg.state}`;
    case "transcript": {
      const n = Array.isArray(msg.sources)
        ? msg.sources.length
        : Array.isArray(msg.citations)
          ? msg.citations.length
          : 0;
      return `transcript ${msg.role} "${clip(String(msg.text), 120)}"${n ? ` (${n} source${n > 1 ? "s" : ""})` : ""}`;
    }
    case "tts_chunk":
      return `tts_chunk #${msg.seq} ${msg.format} ${typeof msg.data_b64 === "string" ? Math.round((msg.data_b64.length * 3) / 4096) : "?"} KB`;
    case "resume_brief":
      return `resume_brief ${msg.segment_id}`;
    case "bookmark_created":
      return `bookmark_created ${msg.question_id}`;
    case "error":
      return `error ${msg.code}: ${clip(String(msg.message), 160)}`;
    default:
      return clip(JSON.stringify(msg), 200);
  }
}

function clip(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n)}…` : s;
}
