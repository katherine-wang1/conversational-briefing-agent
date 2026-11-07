import type { TtsChunk } from "../contracts/session";
import { b64ToBlobUrl, mimeFor, type AudioOutput } from "./output";

interface Item {
  seq: number;
  url: string;
  sentence_id: string;
}

export interface AnswerQueueEvents {
  onStart?(): void;
  onSentence?(sentenceId: string): void;
  /** The queue ran dry (all received chunks played). More may still arrive. */
  onIdle?(): void;
}

/**
 * Plays streamed `tts_chunk`s in `seq` order through the shared AudioOutput. Chunks arriving out
 * of order are inserted by seq; chunks are played as soon as they arrive (no waiting for gaps,
 * since a lost chunk must not stall the answer).
 */
export class AnswerQueue {
  private pending: Item[] = [];
  private active = false;
  private gen = 0;
  private idleWaiters: (() => void)[] = [];

  constructor(
    private out: AudioOutput,
    private ev: AnswerQueueEvents = {},
  ) {}

  get busy(): boolean {
    return this.active || this.pending.length > 0;
  }

  push(chunk: TtsChunk): void {
    const item: Item = {
      seq: chunk.seq,
      url: b64ToBlobUrl(chunk.data_b64, mimeFor(chunk.format)),
      sentence_id: chunk.sentence_id,
    };
    const at = this.pending.findIndex((p) => p.seq > item.seq);
    if (at === -1) this.pending.push(item);
    else this.pending.splice(at, 0, item);
    if (!this.active) void this.pump(this.gen);
  }

  /** Stop the current answer audio and drop everything queued. */
  clear(): void {
    this.gen++;
    for (const p of this.pending) URL.revokeObjectURL(p.url);
    this.pending = [];
    if (this.active && this.out.owner === "answer") this.out.stop();
    this.active = false;
    this.flushIdle();
  }

  /** Resolves when nothing is playing or queued. */
  whenIdle(): Promise<void> {
    if (!this.busy) return Promise.resolve();
    return new Promise((r) => this.idleWaiters.push(r));
  }

  private async pump(gen: number) {
    this.active = true;
    let started = false;
    while (gen === this.gen && this.pending.length) {
      const item = this.pending.shift()!;
      if (!started) {
        started = true;
        this.ev.onStart?.();
      }
      this.ev.onSentence?.(item.sentence_id);
      const result = await this.out.play(item.url, 0, "answer", item.sentence_id);
      URL.revokeObjectURL(item.url);
      if (result === "stopped" && gen === this.gen) {
        // Something else took over the output (e.g. the brief resumed): drop the rest.
        this.clear();
        return;
      }
    }
    if (gen !== this.gen) return;
    this.active = false;
    this.ev.onIdle?.();
    this.flushIdle();
  }

  private flushIdle() {
    const w = this.idleWaiters;
    this.idleWaiters = [];
    for (const r of w) r();
  }
}
