/**
 * End-of-speech logic, independent of the VAD model. Fed one boolean (speech / not speech) per
 * audio frame; decides when the user started talking and when they stopped.
 *
 * - speech starts after `onsetMs` of (mostly) voiced frames;
 * - speech ends after `silenceMs` of continuous silence (default 700 ms);
 * - the utterance is capped at `maxUtteranceMs` (default 30 s);
 * - if nobody speaks within `noSpeechTimeoutMs`, it ends with reason "no_speech".
 */

export interface EndpointerOptions {
  silenceMs: number;
  maxUtteranceMs: number;
  noSpeechTimeoutMs: number;
  onsetMs: number;
}

export type EndpointEvent =
  | { type: "speech_start"; atMs: number }
  | { type: "end"; reason: "silence" | "max" | "no_speech"; atMs: number; speechMs: number };

export class Endpointer {
  private elapsed = 0;
  private voicedRun = 0;
  private silenceRun = 0;
  private startedAt: number | null = null;
  private done = false;

  constructor(public opts: EndpointerOptions) {}

  get started(): boolean {
    return this.startedAt !== null;
  }

  get ended(): boolean {
    return this.done;
  }

  reset(): void {
    this.elapsed = 0;
    this.voicedRun = 0;
    this.silenceRun = 0;
    this.startedAt = null;
    this.done = false;
  }

  feed(isSpeech: boolean, frameMs: number): EndpointEvent[] {
    if (this.done) return [];
    const out: EndpointEvent[] = [];
    this.elapsed += frameMs;

    if (this.startedAt === null) {
      // Leaky integrator: brief dropouts inside a word don't reset onset completely.
      this.voicedRun = isSpeech ? this.voicedRun + frameMs : Math.max(0, this.voicedRun - frameMs);
      if (this.voicedRun >= this.opts.onsetMs) {
        this.startedAt = this.elapsed - this.voicedRun;
        this.silenceRun = 0;
        out.push({ type: "speech_start", atMs: this.startedAt });
      } else if (this.elapsed >= this.opts.noSpeechTimeoutMs) {
        this.done = true;
        out.push({ type: "end", reason: "no_speech", atMs: this.elapsed, speechMs: 0 });
        return out;
      }
    } else {
      this.silenceRun = isSpeech ? 0 : this.silenceRun + frameMs;
      if (this.silenceRun >= this.opts.silenceMs) {
        this.done = true;
        out.push({
          type: "end",
          reason: "silence",
          atMs: this.elapsed,
          speechMs: this.elapsed - this.silenceRun - this.startedAt,
        });
        return out;
      }
    }

    if (this.elapsed >= this.opts.maxUtteranceMs) {
      this.done = true;
      const startedAt = this.startedAt ?? this.elapsed;
      out.push({ type: "end", reason: "max", atMs: this.elapsed, speechMs: this.elapsed - startedAt });
    }
    return out;
  }
}
