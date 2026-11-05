// TypeScript mirror of app/contracts/session.py (TDD §12.5). Keep in sync by hand; the Python file
// is the source of truth. src/contracts/session.test.ts checks the field lists against the .py file.

export type SessionState =
  | "READY"
  | "PLAYING_BRIEF"
  | "LISTENING"
  | "THINKING"
  | "SPEAKING_ANSWER"
  | "OPEN_FLOOR"
  | "ENDED";

export const SESSION_STATES: readonly SessionState[] = [
  "READY",
  "PLAYING_BRIEF",
  "LISTENING",
  "THINKING",
  "SPEAKING_ANSWER",
  "OPEN_FLOOR",
  "ENDED",
];

// ---- client -> server --------------------------------------------------------------------------

export interface ClientHello {
  type: "hello";
  session_id: string;
  client?: { stt?: "browser" | "server"; tts?: "server" | "browser"; mode?: "voice" | "text" };
}

export interface Playback {
  type: "playback";
  segment_id: string;
  offset_ms: number;
}

export interface Interrupt {
  type: "interrupt";
  segment_id?: string | null; // null if nothing was playing
  offset_ms?: number;
}

export interface Utterance {
  type: "utterance";
  text: string;
  stt?: "browser" | "server" | "sim";
  end_of_speech_ts?: number | null; // epoch seconds
}

export interface UtteranceAudio {
  type: "utterance_audio";
  format: string; // e.g. "webm/opus"
  data_b64: string;
  end_of_speech_ts?: number | null;
}

export type ControlAction = "skip" | "next" | "resume" | "end" | "play";

export interface Control {
  type: "control";
  action: ControlAction;
}

export type ClientMessage = ClientHello | Playback | Interrupt | Utterance | UtteranceAudio | Control;

// ---- server -> client --------------------------------------------------------------------------

export interface StateMsg {
  type: "state";
  state: SessionState;
}

export interface TranscriptMsg {
  type: "transcript";
  role: "user" | "agent";
  text: string;
  turn_id: string;
  citations?: string[];
}

export interface TtsChunk {
  type: "tts_chunk";
  seq: number;
  format: string; // "mp3"
  data_b64: string;
  sentence_id: string;
}

export interface ResumeBrief {
  type: "resume_brief";
  segment_id: string;
}

export interface BookmarkCreated {
  type: "bookmark_created";
  question_id: string;
}

export interface ErrorMsg {
  type: "error";
  code: string;
  message: string;
}

export type ServerMessage = StateMsg | TranscriptMsg | TtsChunk | ResumeBrief | BookmarkCreated | ErrorMsg;

/** Field names per message type, mirrored from the pydantic models (checked by a unit test). */
export const CLIENT_FIELDS: Record<ClientMessage["type"], readonly string[]> = {
  hello: ["type", "session_id", "client"],
  playback: ["type", "segment_id", "offset_ms"],
  interrupt: ["type", "segment_id", "offset_ms"],
  utterance: ["type", "text", "stt", "end_of_speech_ts"],
  utterance_audio: ["type", "format", "data_b64", "end_of_speech_ts"],
  control: ["type", "action"],
};

export const SERVER_FIELDS: Record<ServerMessage["type"], readonly string[]> = {
  state: ["type", "state"],
  transcript: ["type", "role", "text", "turn_id", "citations"],
  tts_chunk: ["type", "seq", "format", "data_b64", "sentence_id"],
  resume_brief: ["type", "segment_id"],
  bookmark_created: ["type", "question_id"],
  error: ["type", "code", "message"],
};

/**
 * Parse and minimally validate a server frame. Returns null (and the caller logs) for unknown or
 * malformed frames rather than throwing: the backend may add new message types additively.
 */
export function parseServerMessage(raw: string): ServerMessage | null {
  let obj: unknown;
  try {
    obj = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!obj || typeof obj !== "object") return null;
  const m = obj as Record<string, unknown>;
  switch (m.type) {
    case "state":
      return typeof m.state === "string" && (SESSION_STATES as readonly string[]).includes(m.state)
        ? (m as unknown as StateMsg)
        : null;
    case "transcript":
      return (m.role === "user" || m.role === "agent") && typeof m.text === "string"
        ? ({ citations: [], turn_id: "", ...m } as unknown as TranscriptMsg)
        : null;
    case "tts_chunk":
      return typeof m.seq === "number" && typeof m.data_b64 === "string"
        ? (m as unknown as TtsChunk)
        : null;
    case "resume_brief":
      return typeof m.segment_id === "string" ? (m as unknown as ResumeBrief) : null;
    case "bookmark_created":
      return typeof m.question_id === "string" ? (m as unknown as BookmarkCreated) : null;
    case "error":
      return { type: "error", code: String(m.code ?? "unknown"), message: String(m.message ?? "") };
    default:
      return null;
  }
}
