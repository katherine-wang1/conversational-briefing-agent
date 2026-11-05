/**
 * Browser STT via the Web Speech API (`webkitSpeechRecognition`, Safari and Chrome).
 * Started on interrupt; when the VAD says the user stopped talking we call stop() and take the
 * final result (or the latest interim one if Safari never finalises).
 */

interface SRAlternative {
  transcript: string;
}
interface SRResult {
  isFinal: boolean;
  0: SRAlternative;
  length: number;
}
interface SREvent {
  resultIndex: number;
  results: { length: number; [i: number]: SRResult };
}
interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  onresult: ((e: SREvent) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}
type SRCtor = new () => SpeechRecognitionLike;

function ctor(): SRCtor | null {
  const w = window as unknown as { SpeechRecognition?: SRCtor; webkitSpeechRecognition?: SRCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export function browserSttSupported(): boolean {
  return ctor() !== null;
}

export class BrowserStt {
  private rec: SpeechRecognitionLike | null = null;
  private finals: string[] = [];
  private interim = "";
  private ended = false;
  private endWaiters: (() => void)[] = [];
  error: string | null = null;

  constructor(
    private onInterim: (text: string) => void = () => {},
    private lang = navigator.language || "en-US",
  ) {}

  /** Call inside (or right after) the interrupt tap. */
  start(): void {
    const C = ctor();
    if (!C) throw new Error("Web Speech API not available");
    this.finals = [];
    this.interim = "";
    this.ended = false;
    this.error = null;
    const rec = new C();
    rec.lang = this.lang;
    rec.continuous = true;
    rec.interimResults = true;
    rec.maxAlternatives = 1;
    rec.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) this.finals.push(r[0].transcript.trim());
        else interim += r[0].transcript;
      }
      this.interim = interim.trim();
      this.onInterim(this.text());
    };
    rec.onerror = (e) => {
      // "no-speech" and "aborted" are normal outcomes here.
      if (e.error !== "no-speech" && e.error !== "aborted") this.error = e.error;
    };
    rec.onend = () => {
      this.ended = true;
      const w = this.endWaiters;
      this.endWaiters = [];
      w.forEach((f) => f());
    };
    this.rec = rec;
    rec.start();
  }

  text(): string {
    return [...this.finals, this.interim].filter(Boolean).join(" ").trim();
  }

  /** Stop and wait (up to `timeoutMs`) for the final result. */
  async finish(timeoutMs = 1200): Promise<string> {
    const rec = this.rec;
    if (!rec) return this.text();
    if (!this.ended) {
      const done = new Promise<void>((r) => this.endWaiters.push(r));
      try {
        rec.stop();
      } catch {
        /* already stopped */
      }
      await Promise.race([done, new Promise((r) => setTimeout(r, timeoutMs))]);
    }
    this.rec = null;
    return this.text();
  }

  abort(): void {
    try {
      this.rec?.abort();
    } catch {
      /* ignore */
    }
    this.rec = null;
  }
}
