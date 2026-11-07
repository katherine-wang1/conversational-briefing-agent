import type { Mood } from "../components/Buddy";
import type { ConvState } from "../session/conversation";

/** What the talk button shows: the character's mood, the big label, a hint, and the pill. */
interface TalkView {
  mood: Mood;
  label: string;
  hint: string;
  pill: { text: string; kind: "talk" | "done" | "play" } | null;
}

export function talkView(
  s: Pick<ConvState, "phase" | "speechDetected" | "pendingResume" | "interruptedAt" | "segmentIndex">,
  autoResume: boolean,
  loaded: boolean,
): TalkView {
  switch (s.phase) {
    case "idle":
      return { mood: "idle", label: loaded ? "" : "Loading…", hint: "", pill: null };
    case "ready":
      return {
        mood: "ready",
        label: s.segmentIndex > 0 ? "Resume today's brief" : "Play today's brief",
        hint: "Then tap me any time to ask a question",
        pill: null,
      };
    case "playing":
      return {
        mood: "playing",
        label: "Got a question?",
        hint: "Tap to talk. I'll pause the brief and listen.",
        pill: { text: "Tap to talk", kind: "talk" },
      };
    case "listening":
      return s.speechDetected
        ? {
            mood: "hearing",
            label: "Listening…",
            hint: "I'll answer when you pause",
            pill: { text: "I'm done", kind: "done" },
          }
        : {
            mood: "listening",
            label: "Listening…",
            hint: "Go ahead, ask me anything",
            pill: { text: "I'm done", kind: "done" },
          };
    case "thinking":
      return { mood: "thinking", label: "Thinking…", hint: "Looking at today's sources", pill: null };
    case "answering":
      return {
        mood: "speaking",
        label: "Speaking…",
        hint: "Tap to cut in with another question",
        pill: { text: "Tap to talk", kind: "talk" },
      };
    case "paused":
      if (autoResume && (s.pendingResume !== null || s.interruptedAt))
        return {
          mood: "resuming",
          label: "Back to the brief…",
          hint: "Tap to ask something else",
          pill: { text: "Tap to talk", kind: "talk" },
        };
      return { mood: "idle", label: "Tap to talk", hint: "The brief is paused", pill: { text: "Play", kind: "play" } };
    case "open_floor":
      return {
        mood: "idle",
        label: "Tap to talk",
        hint: "That's the brief. Ask about anything.",
        pill: { text: "Replay", kind: "play" },
      };
    case "ended":
      return { mood: "idle", label: "Session ended", hint: "", pill: null };
  }
}

/** Accessible name of the whole talk area (it is one big button). */
export function talkAria(v: TalkView, phase: ConvState["phase"]): string {
  if (phase === "ready") return v.label;
  if (phase === "listening") return "Listening. Tap when you're done.";
  if (phase === "idle" || phase === "ended") return v.label || "Talk";
  return "Tap to talk";
}
