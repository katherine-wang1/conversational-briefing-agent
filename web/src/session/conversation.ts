/**
 * The client side of a live session: brief playback, two-stage interruption, STT, the answer
 * audio queue and the WebSocket protocol (TDD §12.5, §13.2). UI-independent; React reads `state`.
 */
import type { AudioManifest, BriefScript } from "../contracts/brief";
import type { ServerMessage, SessionState } from "../contracts/session";
import { AnswerQueue } from "../audio/answerQueue";
import { BriefPlayer, type Position } from "../audio/briefPlayer";
import { BrowserStt, browserSttSupported } from "../audio/browserStt";
import { Endpointer, type EndpointEvent } from "../audio/endpointer";
import {
  installMediaSession,
  setNowPlaying,
  setPlaybackState,
} from "../audio/mediaSession";
import {
  blobToB64,
  closeMic,
  MicError,
  openMic,
  protocolFormat,
  UtteranceRecorder,
} from "../audio/mic";
import { AudioOutput } from "../audio/output";
import { EnergyDetector, SileroDetector, type VoiceDetector } from "../audio/vad";
import { resolveAudioUrl } from "../lib/api";
import type { ClientSettings } from "../lib/settings";
import { Store } from "../lib/store";
import { clearProgress, loadProgress, saveProgress } from "./progress";
import { SessionSocket } from "./socket";

export type Phase =
  | "idle" // nothing loaded / not started
  | "ready" // brief loaded, not started
  | "playing" // brief audio
  | "listening" // mic open, waiting for end of speech
  | "thinking" // utterance sent, waiting for the answer
  | "answering" // answer audio
  | "paused" // brief interrupted, waiting for the user (no auto-resume)
  | "open_floor" // brief finished
  | "ended";

export type InterruptSource = "tap" | "button" | "media" | "voice";

export interface TranscriptLine {
  id: string;
  role: "user" | "agent";
  text: string;
  citations: string[];
  pending?: boolean;
}

export interface ConvState {
  phase: Phase;
  serverState: SessionState | null;
  conn: "connecting" | "open" | "closed" | null;
  sessionId: string | null;
  segmentIndex: number;
  storyRef: string | null;
  transcript: TranscriptLine[];
  interim: string;
  speechDetected: boolean;
  interruptedAt: Position | null;
  pendingResume: string | null;
  openQuestions: number;
  notice: string | null;
  error: string | null;
  vadEngine: string | null;
  /** End of speech -> first answer audio, ms (TDD §12.3 latency target). */
  lastLatencyMs: number | null;
  /** Interrupt tap -> brief audio paused, ms. */
  lastInterruptMs: number | null;
}

const INITIAL: ConvState = {
  phase: "idle",
  serverState: null,
  conn: null,
  sessionId: null,
  segmentIndex: 0,
  storyRef: null,
  transcript: [],
  interim: "",
  speechDetected: false,
  interruptedAt: null,
  pendingResume: null,
  openQuestions: 0,
  notice: null,
  error: null,
  vadEngine: null,
  lastLatencyMs: null,
  lastInterruptMs: null,
};

export interface ConversationDeps {
  createSession(briefId: string): Promise<{ session_id: string; ws_url: string }>;
  wsUrl(wsUrl: string): string;
  settings(): ClientSettings;
  output?: AudioOutput;
}

export class Conversation {
  readonly state = new Store<ConvState>({ ...INITIAL });
  readonly out: AudioOutput;
  private player: BriefPlayer | null = null;
  private answers: AnswerQueue;
  private socket: SessionSocket | null = null;
  private script: BriefScript | null = null;
  private ctx: AudioContext | null = null;
  private silero: SileroDetector | null = null;
  private energy: EnergyDetector | null = null;

  // mic / listening
  private stream: MediaStream | null = null;
  private detector: VoiceDetector | null = null;
  private frameHandler: ((isSpeech: boolean, frameMs: number) => void) | null = null;
  private stt: BrowserStt | null = null;
  private recorder: UtteranceRecorder | null = null;
  private listenGen = 0;

  private acceptingTts = false;
  private endOfSpeechAt: number | null = null;
  private thinkingTimer: ReturnType<typeof setTimeout> | null = null;
  private resumeTimer: ReturnType<typeof setTimeout> | null = null;
  private uninstallMedia: (() => void) | null = null;
  private localTurn = 0;

  constructor(private deps: ConversationDeps) {
    this.out = deps.output ?? new AudioOutput();
    this.answers = new AnswerQueue(this.out, {
      onStart: () => {
        this.clearThinkingTimer();
        if (this.endOfSpeechAt !== null) {
          this.patch({ lastLatencyMs: Math.round(performance.now() - this.endOfSpeechAt) });
          this.endOfSpeechAt = null;
        }
        this.patch({ phase: "answering" });
        setPlaybackState("playing");
        this.maybeArmBargeIn();
      },
      onIdle: () => this.afterAnswer(),
    });
  }

  private get s(): ConvState {
    return this.state.get();
  }

  private patch(p: Partial<ConvState>) {
    this.state.patch(p);
  }

  // ---- lifecycle -------------------------------------------------------------------------------

  load(script: BriefScript | null, manifest: AudioManifest): void {
    this.player?.dispose();
    this.script = script;
    this.player = new BriefPlayer(
      this.out,
      manifest.segments,
      {
        onSegment: (i, seg) => {
          this.patch({ segmentIndex: i, storyRef: seg.story_ref ?? null });
          saveProgress(manifest.brief_id, i);
          const story = this.script?.stories.find((st) => st.story_ref === seg.story_ref);
          setNowPlaying(story?.headline ?? "Today's briefing");
        },
        onPosition: (pos) => this.socket?.send({ type: "playback", ...pos }),
        onEnded: () => {
          clearProgress();
          setPlaybackState("paused");
          if (this.s.phase === "playing") this.patch({ phase: "open_floor" });
          this.maybeArmBargeIn();
        },
      },
      resolveAudioUrl,
    );
    // Resume where this device left off in this brief (reload, or iOS evicting the tab).
    const saved = loadProgress(manifest.brief_id);
    const at = saved !== null && saved < manifest.segments.length ? saved : 0;
    if (this.s.phase === "idle" && at > 0) this.patch({ segmentIndex: at, storyRef: manifest.segments[at].story_ref ?? null });
    void this.player.preload(at);
    if (this.s.phase === "idle") this.patch({ phase: "ready" });
  }

  /** Big play button. Must be called from a tap handler (unlocks iOS audio). */
  async play(briefId: string): Promise<void> {
    this.unlock();
    const st = this.s;
    if (st.phase === "paused" && st.interruptedAt) {
      this.resumeBrief(st.pendingResume ?? st.interruptedAt.segment_id, true);
      return;
    }
    if (st.phase === "playing" || st.phase === "answering") return;
    if (!this.socket) await this.connect(briefId);
    this.socket?.send({ type: "control", action: "play" });
    this.patch({ phase: "playing", interruptedAt: null, pendingResume: null, error: null });
    setPlaybackState("playing");
    const start = st.phase === "open_floor" || st.phase === "ended" ? 0 : this.s.segmentIndex;
    this.player?.playFrom(start, 0);
    this.maybeArmBargeIn();
  }

  private async connect(briefId: string) {
    const { session_id, ws_url } = await this.deps.createSession(briefId);
    this.patch({ sessionId: session_id });
    const st = this.deps.settings();
    this.socket = new SessionSocket(
      this.deps.wsUrl(ws_url),
      () => ({
        type: "hello",
        session_id,
        client: { stt: st.stt_mode, tts: "server", mode: "voice" },
      }),
      {
        onMessage: (m) => this.onServer(m),
        onStatus: (conn) => this.patch({ conn }),
      },
    );
    this.socket.connect();
    this.uninstallMedia = installMediaSession({
      onPause: () => this.interrupt("media"),
      onPlay: () => {
        if (this.s.phase === "listening") this.finishListeningNow();
        else this.interrupt("media");
      },
      onNext: () => this.skip(),
    });
  }

  end(): void {
    this.socket?.send({ type: "control", action: "end" });
    this.stopEverything();
    this.patch({ phase: "ended" });
    setPlaybackState("none");
    setTimeout(() => this.socket?.close(), 300);
  }

  dispose(): void {
    this.stopEverything();
    this.socket?.close();
    this.socket = null;
    this.player?.dispose();
    this.uninstallMedia?.();
    void this.silero?.stop();
    void this.ctx?.close().catch(() => {});
  }

  /** Expose the frame log for the debug panel / e2e test. */
  get frames() {
    return this.socket?.log ?? [];
  }

  // ---- audio unlock ----------------------------------------------------------------------------

  /** Call synchronously in every tap handler: iOS needs a user gesture to start audio/mic. */
  unlock(): void {
    this.out.unlock();
    const ctx = this.audioContext();
    if (ctx.state === "suspended") void ctx.resume().catch(() => {});
    if (this.deps.settings().vad_engine === "silero") void this.sileroDetector().warmUp();
  }

  private audioContext(): AudioContext {
    if (!this.ctx || this.ctx.state === "closed") {
      const AC =
        window.AudioContext ??
        (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      this.ctx = new AC();
    }
    return this.ctx;
  }

  private sileroDetector(): SileroDetector {
    if (!this.silero) this.silero = new SileroDetector(() => this.audioContext());
    return this.silero;
  }

  // ---- interruption ----------------------------------------------------------------------------

  /** Tap on the talk area / button / AirPods / voice onset. */
  interrupt(source: InterruptSource): void {
    const t0 = performance.now();
    if (source !== "voice") this.unlock();
    const phase = this.s.phase;
    if (phase === "idle" || phase === "ended" || !this.socket) return;
    if (phase === "listening") {
      // Second tap while listening = "I'm done" (VAD is the normal path; this is a fallback).
      if (source !== "voice") this.finishListeningNow();
      return;
    }

    this.clearResumeTimer();
    this.clearThinkingTimer();
    this.acceptingTts = false;

    if (phase === "playing") {
      const pos = this.player?.pause() ?? null;
      this.patch({ interruptedAt: pos, pendingResume: null, lastInterruptMs: Math.round(performance.now() - t0) });
      this.socket?.send({ type: "interrupt", segment_id: pos?.segment_id ?? null, offset_ms: pos?.offset_ms ?? 0 });
    } else {
      if (phase === "answering") this.answers.clear();
      this.out.stop();
      this.patch({ lastInterruptMs: Math.round(performance.now() - t0) });
      this.socket?.send({ type: "interrupt", segment_id: null, offset_ms: 0 });
    }
    setPlaybackState("paused");
    void this.startListening(source);
  }

  private async startListening(source: InterruptSource) {
    const gen = ++this.listenGen;
    const st = this.deps.settings();
    this.patch({ phase: "listening", interim: "", speechDetected: source === "voice", notice: null, error: null });

    // Browser STT must start inside the gesture on iOS: do it before any await.
    this.stt = null;
    if (st.stt_mode === "browser") {
      if (browserSttSupported()) {
        const stt = new BrowserStt((text) => this.patch({ interim: text }));
        try {
          stt.start();
          this.stt = stt;
        } catch (err) {
          this.patch({ notice: `Speech recognition unavailable (${String(err)}); using server STT.` });
        }
      } else {
        this.patch({ notice: "This browser has no speech recognition; using server STT." });
      }
    }

    try {
      await this.ensureMic();
    } catch (err) {
      if (gen !== this.listenGen) return;
      this.stt?.abort();
      this.stt = null;
      const msg = err instanceof MicError ? err.message : `Microphone error: ${String(err)}`;
      this.patch({ error: msg });
      this.backToBrief(false);
      return;
    }
    if (gen !== this.listenGen) return;

    if (!this.stt && UtteranceRecorder.supported() && this.stream) {
      this.recorder = new UtteranceRecorder();
      try {
        this.recorder.start(this.stream);
      } catch (err) {
        this.recorder = null;
        this.patch({ error: `Can't record audio: ${String(err)}` });
      }
    }

    const ep = new Endpointer({
      silenceMs: st.silence_ms,
      maxUtteranceMs: st.max_utterance_ms,
      noSpeechTimeoutMs: st.no_speech_timeout_ms,
      onsetMs: 90,
    });
    if (source === "voice") ep.feed(true, ep.opts.onsetMs); // barge-in already heard the onset
    this.frameHandler = (isSpeech, frameMs) => {
      for (const e of ep.feed(isSpeech, frameMs)) this.onEndpoint(gen, e);
    };
  }

  private onEndpoint(gen: number, e: EndpointEvent) {
    if (gen !== this.listenGen) return;
    if (e.type === "speech_start") this.patch({ speechDetected: true });
    else void this.finishListening(gen, e.reason);
  }

  /** Manual end of utterance (second tap / AirPods while listening). */
  finishListeningNow(): void {
    if (this.s.phase !== "listening") return;
    void this.finishListening(this.listenGen, "manual");
  }

  private async finishListening(gen: number, reason: string) {
    if (gen !== this.listenGen || this.s.phase !== "listening") return;
    this.listenGen++;
    this.frameHandler = null;
    this.endOfSpeechAt = performance.now();
    const endTs = Date.now() / 1000;
    const heardSpeech = this.s.speechDetected || reason === "manual";
    this.patch({ phase: "thinking" });

    const stt = this.stt;
    const recorder = this.recorder;
    this.stt = null;
    this.recorder = null;
    const textP = stt ? stt.finish() : Promise.resolve("");
    const blobP = recorder ? recorder.stop() : Promise.resolve(null);
    const [text, blob] = await Promise.all([textP, blobP]);
    if (!this.deps.settings().headphones_mode) this.releaseMic();

    if (stt) {
      if (!text) return this.nothingHeard(stt.error);
      this.sendUserText(text, endTs);
      return;
    }
    if (!blob || !heardSpeech) return this.nothingHeard(null);
    this.acceptingTts = true;
    this.armThinkingTimer();
    this.socket?.send({
      type: "utterance_audio",
      format: protocolFormat(blob.type || "audio/webm"),
      data_b64: await blobToB64(blob),
      end_of_speech_ts: endTs,
    });
    this.addLine({ role: "user", text: "…", citations: [], pending: true });
  }

  private sendUserText(text: string, endTs: number) {
    this.acceptingTts = true;
    this.armThinkingTimer();
    this.patch({ interim: "" });
    this.addLine({ role: "user", text, citations: [], pending: true });
    this.socket?.send({ type: "utterance", text, stt: "browser", end_of_speech_ts: endTs });
  }

  /** Typed question (accessibility / no-mic fallback): same path as a spoken one. */
  sendText(text: string): void {
    const t = text.trim();
    if (!t || !this.socket) return;
    if (this.s.phase === "playing" || this.s.phase === "answering") this.interruptSilently();
    this.endOfSpeechAt = performance.now();
    this.patch({ phase: "thinking" });
    this.sendUserText(t, Date.now() / 1000);
  }

  private interruptSilently() {
    if (this.s.phase === "playing") {
      const pos = this.player?.pause() ?? null;
      this.patch({ interruptedAt: pos });
      this.socket?.send({ type: "interrupt", segment_id: pos?.segment_id ?? null, offset_ms: pos?.offset_ms ?? 0 });
    } else {
      this.answers.clear();
      this.socket?.send({ type: "interrupt", segment_id: null, offset_ms: 0 });
    }
  }

  private nothingHeard(sttError: string | null) {
    this.endOfSpeechAt = null;
    this.patch({
      interim: "",
      notice: sttError ? `Didn't catch that (speech recognition: ${sttError}).` : "Didn't catch that.",
    });
    this.backToBrief(true);
  }

  /** Return to whatever was happening before the interrupt. */
  private backToBrief(tellServer: boolean) {
    const at = this.s.interruptedAt;
    if (at && this.deps.settings().auto_resume) {
      if (tellServer) this.socket?.send({ type: "control", action: "resume" });
      this.resumeBrief(at.segment_id, false);
    } else if (at) {
      this.patch({ phase: "paused", pendingResume: at.segment_id });
    } else {
      this.patch({ phase: this.player && this.s.segmentIndex >= this.player.segments.length - 1 ? "open_floor" : "paused" });
    }
  }

  // ---- mic -------------------------------------------------------------------------------------

  private async ensureMic(): Promise<void> {
    if (this.stream && this.stream.getAudioTracks().some((t) => t.readyState === "live")) return;
    this.stream = await openMic();
    const handler = (isSpeech: boolean, ms: number) => this.frameHandler?.(isSpeech, ms);
    const want = this.deps.settings().vad_engine;
    if (want === "silero") {
      try {
        const d = this.sileroDetector();
        await d.start(this.stream, handler);
        this.detector = d;
        this.patch({ vadEngine: "silero" });
        return;
      } catch (err) {
        console.warn("Silero VAD failed; falling back to energy VAD", err);
        this.patch({ notice: "Using the simple voice detector (Silero VAD failed to load)." });
      }
    }
    this.energy ??= new EnergyDetector(() => this.audioContext());
    await this.energy.start(this.stream, handler);
    this.detector = this.energy;
    this.patch({ vadEngine: "energy" });
  }

  private releaseMic() {
    void this.detector?.stop();
    this.detector = null;
    this.frameHandler = null;
    closeMic(this.stream);
    this.stream = null;
  }

  /** Stage 2 (headphones mode): keep the mic open during playback; voice onset interrupts. */
  private maybeArmBargeIn() {
    const st = this.deps.settings();
    if (!st.headphones_mode) return;
    const phase = this.s.phase;
    if (phase !== "playing" && phase !== "answering") return;
    const gen = this.listenGen;
    void this.ensureMic()
      .then(() => {
        if (gen !== this.listenGen) return;
        const ep = new Endpointer({
          silenceMs: 1e9,
          maxUtteranceMs: 1e9,
          noSpeechTimeoutMs: 1e9,
          onsetMs: st.barge_in_onset_ms,
        });
        this.frameHandler = (isSpeech, ms) => {
          const p = this.s.phase;
          if (p !== "playing" && p !== "answering") return;
          if (ep.feed(isSpeech, ms).some((e) => e.type === "speech_start")) this.interrupt("voice");
        };
      })
      .catch((err) => this.patch({ notice: `Headphones mode: mic unavailable (${String(err)})` }));
  }

  // ---- server messages -------------------------------------------------------------------------

  private onServer(m: ServerMessage) {
    switch (m.type) {
      case "state":
        this.patch({ serverState: m.state });
        if (m.state === "ENDED" && this.s.phase !== "ended") {
          this.stopEverything();
          this.patch({ phase: "ended" });
        }
        break;
      case "transcript":
        this.onTranscript(m.role, m.text, m.turn_id, m.citations ?? []);
        break;
      case "tts_chunk":
        if (!this.acceptingTts) return; // stale audio from an interrupted answer
        this.answers.push(m);
        break;
      case "resume_brief":
        this.onResumeBrief(m.segment_id);
        break;
      case "bookmark_created":
        this.patch({ openQuestions: this.s.openQuestions + 1 });
        break;
      case "error":
        this.clearThinkingTimer();
        this.patch({ error: `${m.code}: ${m.message}` });
        if (this.s.phase === "thinking") this.backToBrief(false);
        break;
    }
  }

  private onTranscript(role: "user" | "agent", text: string, turnId: string, citations: string[]) {
    const lines = [...this.s.transcript];
    if (role === "user") {
      const i = lines.findIndex((l) => l.role === "user" && l.pending);
      const line = { id: turnId || `u${++this.localTurn}`, role, text, citations };
      if (i >= 0) lines[i] = line;
      else lines.push(line);
    } else {
      const last = lines[lines.length - 1];
      if (last && last.role === "agent" && turnId && last.id === turnId) {
        lines[lines.length - 1] = {
          ...last,
          text: text.startsWith(last.text) ? text : `${last.text} ${text}`,
          citations: [...new Set([...last.citations, ...citations])],
        };
      } else lines.push({ id: turnId || `a${++this.localTurn}`, role, text, citations });
    }
    this.patch({ transcript: lines });
  }

  private onResumeBrief(segmentId: string) {
    if (this.s.phase === "playing") return; // already back in the brief
    this.patch({ pendingResume: segmentId });
    if (!this.answers.busy && this.s.phase !== "thinking" && this.s.phase !== "listening") this.afterAnswer();
  }

  private afterAnswer() {
    setPlaybackState("paused");
    const seg = this.s.pendingResume;
    if (seg && this.deps.settings().auto_resume) {
      // Short pause, then back to the start of the interrupted sentence (TDD §12.1).
      this.clearResumeTimer();
      this.patch({ phase: "paused" });
      this.resumeTimer = setTimeout(() => {
        if (this.s.phase === "paused" && this.s.pendingResume === seg) this.resumeBrief(seg, false);
      }, 600);
      return;
    }
    if (this.s.phase === "answering" || this.s.phase === "thinking") {
      this.patch({ phase: this.s.interruptedAt ? "paused" : "open_floor" });
    }
  }

  /** Resume the brief at the start of `segmentId`. */
  resumeBrief(segmentId: string, userInitiated: boolean): void {
    if (userInitiated) {
      this.unlock();
      this.socket?.send({ type: "control", action: "resume" });
    }
    const idx = this.player?.indexOf(segmentId) ?? -1;
    const at = idx >= 0 ? idx : this.s.segmentIndex;
    this.clearResumeTimer();
    this.acceptingTts = false;
    this.answers.clear();
    this.patch({ phase: "playing", pendingResume: null, interruptedAt: null });
    setPlaybackState("playing");
    this.player?.playFrom(at, 0);
    this.maybeArmBargeIn();
  }

  // ---- navigation ------------------------------------------------------------------------------

  skip(): void {
    if (!this.player) return;
    this.unlock();
    this.socket?.send({ type: "control", action: "skip" });
    this.jumpTo(this.player.nextStoryIndex());
  }

  next(): void {
    if (!this.player) return;
    this.unlock();
    this.socket?.send({ type: "control", action: "next" });
    this.jumpTo(this.player.index + 1);
  }

  private jumpTo(index: number) {
    if (this.s.phase === "listening") {
      this.listenGen++;
      this.stt?.abort();
      this.recorder?.cancel();
      if (!this.deps.settings().headphones_mode) this.releaseMic();
    }
    this.answers.clear();
    this.acceptingTts = false;
    this.clearResumeTimer();
    this.clearThinkingTimer();
    this.patch({ phase: "playing", interruptedAt: null, pendingResume: null });
    this.player?.playFrom(index, 0);
    this.maybeArmBargeIn();
  }

  // ---- helpers ---------------------------------------------------------------------------------

  private addLine(l: Omit<TranscriptLine, "id">) {
    this.patch({ transcript: [...this.s.transcript, { ...l, id: `local-${++this.localTurn}` }] });
  }

  private armThinkingTimer() {
    this.clearThinkingTimer();
    this.thinkingTimer = setTimeout(() => {
      if (this.s.phase === "thinking") {
        this.patch({ error: "No answer from the server." });
        this.backToBrief(false);
      }
    }, 20_000);
  }

  private clearThinkingTimer() {
    if (this.thinkingTimer) clearTimeout(this.thinkingTimer);
    this.thinkingTimer = null;
  }

  private clearResumeTimer() {
    if (this.resumeTimer) clearTimeout(this.resumeTimer);
    this.resumeTimer = null;
  }

  private stopEverything() {
    this.listenGen++;
    this.clearResumeTimer();
    this.clearThinkingTimer();
    this.stt?.abort();
    this.stt = null;
    this.recorder?.cancel();
    this.recorder = null;
    this.player?.pause();
    this.answers.clear();
    this.out.stop();
    this.releaseMic();
  }
}
