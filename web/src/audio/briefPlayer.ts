import type { AudioSegment } from "../contracts/brief";
import type { AudioOutput } from "./output";

export interface Position {
  segment_id: string;
  offset_ms: number;
  /** This segment finished playing (on the last one, the brief ended). */
  completed?: boolean;
}

export interface BriefPlayerEvents {
  onSegment?(index: number, seg: AudioSegment): void;
  /** Periodic (every reportIntervalMs) and on segment change. */
  onPosition?(pos: Position): void;
  onEnded?(): void;
}

/**
 * Plays an AudioManifest through the shared AudioOutput, one short segment at a time, preloading
 * the next segment into a blob URL so the switch is gapless enough on mobile networks.
 */
export class BriefPlayer {
  index = 0;
  private run = 0;
  private playing = false;
  private cache = new Map<number, string>();
  private inflight = new Map<number, Promise<string | null>>();
  private timer: ReturnType<typeof setInterval> | null = null;

  constructor(
    private out: AudioOutput,
    readonly segments: AudioSegment[],
    private ev: BriefPlayerEvents = {},
    private resolveUrl: (u: string) => string = (u) => u,
    private reportIntervalMs = 2000,
  ) {}

  get isPlaying(): boolean {
    return this.playing;
  }

  indexOf(segmentId: string): number {
    return this.segments.findIndex((s) => s.sid === segmentId);
  }

  /** Start (or restart) playback at segment `index`, `offsetMs` into it. */
  playFrom(index: number, offsetMs = 0): void {
    if (index < 0 || index >= this.segments.length) {
      this.stopReporting();
      this.playing = false;
      this.ev.onEnded?.();
      return;
    }
    const run = ++this.run;
    this.playing = true;
    this.startReporting();
    void this.loop(run, index, offsetMs);
  }

  /** Pause and return where we were. Returns null if the brief wasn't playing. */
  pause(): Position | null {
    if (!this.playing) return null;
    const pos = this.position();
    this.run++;
    this.playing = false;
    this.stopReporting();
    if (this.out.owner === "brief") this.out.stop();
    return pos;
  }

  position(): Position | null {
    const seg = this.segments[this.index];
    if (!seg) return null;
    return { segment_id: seg.sid, offset_ms: this.playing ? this.out.positionMs() : 0 };
  }

  /** Index of the first segment of the next story after the current one (for "skip"). */
  nextStoryIndex(): number {
    const cur = this.segments[this.index]?.story_ref ?? null;
    for (let i = this.index + 1; i < this.segments.length; i++) {
      const ref = this.segments[i].story_ref ?? null;
      if (ref !== cur && ref !== null) return i;
    }
    return this.segments.length;
  }

  dispose(): void {
    this.pause();
    for (const url of this.cache.values()) URL.revokeObjectURL(url);
    this.cache.clear();
  }

  private async loop(run: number, start: number, offsetMs: number) {
    for (let i = start; i < this.segments.length; i++) {
      if (run !== this.run) return;
      this.index = i;
      const seg = this.segments[i];
      const src = this.cache.get(i) ?? this.resolveUrl(seg.url);
      void this.preload(i + 1);
      this.ev.onSegment?.(i, seg);
      this.ev.onPosition?.({ segment_id: seg.sid, offset_ms: offsetMs });
      const result = await this.out.play(src, offsetMs, "brief");
      offsetMs = 0;
      if (run !== this.run) return;
      if (result === "stopped") return;
      if (result === "ended")
        this.ev.onPosition?.({ segment_id: seg.sid, offset_ms: Math.round(seg.duration_ms), completed: true });
      // "error": skip the broken segment rather than stalling the brief.
      this.evict(i - 1);
    }
    if (run !== this.run) return;
    this.playing = false;
    this.stopReporting();
    this.ev.onEnded?.();
  }

  preload(i: number): Promise<string | null> {
    const seg = this.segments[i];
    if (!seg) return Promise.resolve(null);
    const hit = this.cache.get(i);
    if (hit) return Promise.resolve(hit);
    let p = this.inflight.get(i);
    if (!p) {
      p = fetch(this.resolveUrl(seg.url))
        .then((r) => (r.ok ? r.blob() : null))
        .then((b) => {
          if (!b) return null;
          const url = URL.createObjectURL(b);
          this.cache.set(i, url);
          return url;
        })
        .catch(() => null)
        .finally(() => this.inflight.delete(i));
      this.inflight.set(i, p);
    }
    return p;
  }

  private evict(i: number) {
    const url = this.cache.get(i);
    if (url) {
      URL.revokeObjectURL(url);
      this.cache.delete(i);
    }
  }

  private startReporting() {
    if (this.timer) return;
    this.timer = setInterval(() => {
      if (!this.playing) return;
      const pos = this.position();
      if (pos) this.ev.onPosition?.(pos);
    }, this.reportIntervalMs);
  }

  private stopReporting() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }
}
