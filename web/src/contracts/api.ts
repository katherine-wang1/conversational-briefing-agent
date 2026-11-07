// REST shapes for TDD §11. These are NOT in app/contracts/ yet; they follow the table columns in
// TDD §10.2. The mock server (web/mock) implements exactly these. If W-E's API differs, adapt here.

import type { AudioManifest, BriefScript, Interest } from "./brief";

export interface Settings {
  brief_time: string; // "07:30"
  timezone: string; // IANA
  voice: string; // TTS voice id
  stt_mode: "browser" | "server";
  auto_resume: boolean;
  headphones_mode: boolean; // stage-2 barge-in
  length_pref?: "short" | "medium" | "long";
  depth_pref?: "light" | "standard" | "deep";
}

export interface Me {
  user_id: string;
  email: string;
  display_name?: string | null;
  onboarded: boolean;
  research_consent: boolean;
  settings: Settings;
}

export interface OnboardingRequest {
  interests_text: string;
  length_pref: "short" | "medium" | "long";
  depth_pref: "light" | "standard" | "deep";
  brief_time: string;
  timezone: string;
  research_consent: boolean;
}

export interface TodayBrief {
  // null while the brief is still pending (202) or before onboarding (409 onboarding_required)
  brief_id: string | null;
  status: "pending" | "ready" | "failed" | "onboarding_required";
  script: BriefScript | null;
  manifest: AudioManifest | null;
}

export interface SessionCreated {
  session_id: string;
  ws_url: string; // absolute ws(s):// or a path like /ws/session/{id}
}

export interface HistoryTurn {
  role: "user" | "agent";
  text: string;
  citations?: string[];
}

export interface HistoryItem {
  brief_id: string;
  local_date: string;
  headlines: string[];
  session_id?: string | null;
  turns?: HistoryTurn[];
  citation_urls?: Record<string, string>; // passage_id -> url
}

export interface OpenQuestion {
  id: string;
  question: string;
  status: "open" | "researching" | "answered" | "unanswerable" | "delivered" | "dismissed" | "expired";
  answer?: { text: string } | null;
  citations?: string[];
  created_at: string;
  answered_at?: string | null;
}

export interface RatingRequest {
  session_id: string;
  usefulness: number; // 1-5
  per_story: Record<string, number>; // story_ref -> relevance 1-5
  learned_new: boolean;
  length_fit: "too_short" | "about_right" | "too_long";
  followup_useful: number | null; // 1-5, null if no follow-ups
}

export type { Interest };
