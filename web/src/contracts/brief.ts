// Mirror of the parts of app/contracts/brief.py and app/contracts/memory.py the web app reads.

export type Slot = "big_news" | "for_you" | "outside_usual";

export interface AudioSegment {
  sid: string;
  url: string;
  duration_ms: number;
  story_ref?: string | null;
}

export interface AudioManifest {
  brief_id: string;
  segments: AudioSegment[];
}

export interface BriefSentence {
  sid: string;
  text: string;
  story_ref?: string | null;
  cites?: string[];
  kind: "intro" | "followup_answer" | "story" | "transition" | "outro";
}

export interface BriefStory {
  story_ref: string;
  slot: Slot;
  headline: string;
  why_tag: string;
  aliases?: string[];
}

export interface BriefScript {
  brief_id: string;
  user_id: string;
  local_date: string;
  arm?: string;
  stories: BriefStory[];
  sentences: BriefSentence[];
  est_duration_s: number;
}

export type InterestKind = "topic" | "entity" | "keep_posted";
export type InterestSource = "onboarding" | "explicit" | "implicit" | "exploration";

export interface Interest {
  id: string;
  label: string;
  subtopic: string;
  entity?: string | null;
  kind: InterestKind;
  weight: number;
  confidence: number;
  explicit_pin: boolean;
  shown_n?: number;
  engaged_n?: number;
  last_reinforced_at?: string | null;
  source: InterestSource;
  evidence?: string[];
}
