/**
 * The single HTMLAudioElement that plays everything (brief segments and answer chunks).
 *
 * One element, unlocked once on the first user tap, is the most reliable setup on iOS Safari:
 * a freshly created element that never received a user-gesture play() is blocked by the
 * autoplay policy, and using one element keeps one Media Session (AirPods controls).
 */

export type PlayResult = "ended" | "stopped" | "error";

let silentUrl: string | null = null;

/** A 100 ms silent 8 kHz mono WAV, built at runtime (no asset needed). */
export function silentWavUrl(): string {
  if (silentUrl) return silentUrl;
  const samples = 800;
  const buf = new ArrayBuffer(44 + samples * 2);
  const v = new DataView(buf);
  const w = (o: number, s: string) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  w(0, "RIFF");
  v.setUint32(4, 36 + samples * 2, true);
  w(8, "WAVE");
  w(12, "fmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true); // PCM
  v.setUint16(22, 1, true); // mono
  v.setUint32(24, 8000, true);
  v.setUint32(28, 16000, true);
  v.setUint16(32, 2, true);
  v.setUint16(34, 16, true);
  w(36, "data");
  v.setUint32(40, samples * 2, true);
  silentUrl = URL.createObjectURL(new Blob([buf], { type: "audio/wav" }));
  return silentUrl;
}

/** Playback speeds offered in Settings (TODO 1.21). */
export const PLAYBACK_RATES = [1.0, 1.1, 1.2, 1.3, 1.5] as const;
export const DEFAULT_PLAYBACK_RATE = 1.2;

export function clampRate(r: unknown): number {
  const n = typeof r === "number" && Number.isFinite(r) ? r : DEFAULT_PLAYBACK_RATE;
  return Math.min(2, Math.max(0.8, n));
}

export type OutputEvent = "load" | "playing" | "ended" | "error" | "rate";

export class AudioOutput {
  readonly el: HTMLAudioElement;
  unlocked = false;
  private token = 0;
  private settle: ((r: PlayResult) => void) | null = null;
  private rate = 1;
  private label = "";
  /** What the element is currently playing ("brief" | "answer"), for interrupt routing. */
  owner: "brief" | "answer" | null = null;
  /** Audio events for the live log. */
  onEvent: ((ev: OutputEvent, text: string) => void) | null = null;

  constructor(el?: HTMLAudioElement) {
    this.el = el ?? new Audio();
    this.el.preload = "auto";
    this.el.setAttribute("playsinline", "");
    this.el.addEventListener("ended", () => {
      if (this.owner) this.emit("ended", `${this.owner} ${this.label} ended`);
      this.finish("ended");
    });
    this.el.addEventListener("error", () => {
      if (this.el.src && this.el.src !== silentUrl) {
        this.emit("error", `${this.owner ?? "audio"} ${this.label} error: ${this.el.error?.message || this.el.error?.code || "?"}`);
        this.finish("error");
      }
    });
    // Browsers reset playbackRate to defaultPlaybackRate when a new src loads; set both, and again
    // once the media is loaded / starts, in case a browser (Safari) resets it anyway.
    this.el.addEventListener("loadedmetadata", () => {
      this.applyRate();
      if (this.owner) this.emit("load", `${this.owner} ${this.label} loaded (${Math.round(this.el.duration * 1000) || "?"} ms)`);
    });
    this.el.addEventListener("playing", () => {
      this.applyRate();
      if (this.owner) this.emit("playing", `${this.owner} ${this.label} playing at ${this.el.playbackRate}×`);
    });
  }

  /** Playback speed for everything (brief and answers), pitch preserved. */
  setRate(rate: number): void {
    const r = clampRate(rate);
    if (r === this.rate) return;
    this.rate = r;
    this.applyRate();
    this.emit("rate", `playback rate ${r}×`);
  }

  get playbackRate(): number {
    return this.rate;
  }

  private applyRate(): void {
    const el = this.el as HTMLAudioElement & { webkitPreservesPitch?: boolean; mozPreservesPitch?: boolean };
    try {
      el.preservesPitch = true;
      el.webkitPreservesPitch = true;
      el.mozPreservesPitch = true;
      el.defaultPlaybackRate = this.rate;
      if (el.playbackRate !== this.rate) el.playbackRate = this.rate;
    } catch {
      /* some engines throw on unsupported rates; keep 1.0 */
    }
  }

  private emit(ev: OutputEvent, text: string) {
    try {
      this.onEvent?.(ev, text);
    } catch {
      /* never break playback for the log */
    }
  }

  /**
   * Must be called synchronously inside a user gesture (tap) handler. Plays a silent clip so iOS
   * marks this element as user-activated; later programmatic play() calls are then allowed.
   */
  unlock(): void {
    // Never touch the element while it holds real audio (that would reset its position).
    if (this.unlocked || this.owner !== null || !this.el.paused) return;
    this.unlocked = true;
    try {
      this.el.src = silentWavUrl();
      const p = this.el.play();
      if (p)
        p.catch((err: unknown) => {
          // AbortError = a real play() replaced the silent clip, which is fine.
          if (!(err instanceof DOMException && err.name === "AbortError")) this.unlocked = false;
        });
    } catch {
      this.unlocked = false;
    }
  }

  /** Play `src` from `startMs`. Resolves when it ends, errors, or is stopped/replaced. */
  play(src: string, startMs = 0, owner: "brief" | "answer" = "brief", label = ""): Promise<PlayResult> {
    this.finish("stopped");
    const token = ++this.token;
    this.owner = owner;
    this.label = label;
    return new Promise<PlayResult>((resolve) => {
      this.settle = (r) => {
        if (token === this.token) this.owner = null;
        resolve(r);
      };
      const el = this.el;
      el.src = src;
      this.applyRate();
      if (startMs > 0) {
        const seek = () => {
          if (token !== this.token) return;
          try {
            el.currentTime = startMs / 1000;
          } catch {
            /* ignore */
          }
        };
        if (el.readyState >= 1) seek();
        else el.addEventListener("loadedmetadata", seek, { once: true });
      }
      const p = el.play();
      if (p)
        p.then(() => {
          this.unlocked = true;
        }).catch((err: unknown) => {
          // AbortError = replaced by a newer play()/pause(); anything else is a real failure.
          if (token !== this.token) return;
          if (err instanceof DOMException && err.name === "AbortError") return;
          console.warn("audio play() failed", err);
          this.finish("error");
        });
    });
  }

  /** Stop immediately (pause; the element keeps its unlock). */
  stop(): void {
    this.token++;
    this.owner = null;
    try {
      this.el.pause();
    } catch {
      /* ignore */
    }
    this.finish("stopped");
  }

  positionMs(): number {
    return Math.round((this.el.currentTime || 0) * 1000);
  }

  get playing(): boolean {
    return this.owner !== null && !this.el.paused;
  }

  private finish(r: PlayResult) {
    const s = this.settle;
    this.settle = null;
    if (s) s(r);
  }
}

export function b64ToBlobUrl(b64: string, mime: string): string {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return URL.createObjectURL(new Blob([bytes], { type: mime }));
}

export function mimeFor(format: string): string {
  const f = format.toLowerCase();
  if (f.includes("mp3") || f.includes("mpeg")) return "audio/mpeg";
  if (f.includes("wav")) return "audio/wav";
  if (f.includes("ogg")) return "audio/ogg";
  if (f.includes("webm")) return "audio/webm";
  if (f.includes("aac") || f.includes("mp4") || f.includes("m4a")) return "audio/mp4";
  return "audio/mpeg";
}
