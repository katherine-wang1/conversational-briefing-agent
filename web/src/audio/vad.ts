/**
 * Voice activity detectors. Each one turns a mic MediaStream into a stream of per-frame
 * speech / not-speech decisions; the Endpointer turns those into start / end events.
 *
 * - SileroDetector: @ricky0123/vad-web (Silero ONNX model in an AudioWorklet). Default.
 * - EnergyDetector: RMS against an adaptive noise floor. Fallback when the model can't load
 *   (old iOS, blocked wasm) and in automated tests with synthetic audio.
 */

export type FrameCallback = (isSpeech: boolean, frameMs: number) => void;

export interface VoiceDetector {
  readonly name: "silero" | "energy";
  start(stream: MediaStream, onFrame: FrameCallback): Promise<void>;
  stop(): Promise<void>;
}

// ---- energy --------------------------------------------------------------------------------------

export interface EnergyOptions {
  frameMs: number;
  /** Speech if RMS > max(absFloor, noiseFloor * ratio). */
  ratio: number;
  absFloor: number;
}

/**
 * Pure decision logic for the energy detector (unit-tested). The noise floor is the minimum RMS
 * over a sliding window ("minimum statistics"): speech always has short dips between syllables,
 * so the minimum stays near the room noise while someone talks, but a steady loud room raises it.
 */
export class EnergyGate {
  private window: number[] = [];
  constructor(
    private opts: Pick<EnergyOptions, "ratio" | "absFloor">,
    private windowFrames = 75, // 1.5 s at 20 ms
  ) {}

  get noise(): number {
    return this.window.length ? Math.min(...this.window) : 0;
  }

  decide(rms: number): boolean {
    this.window.push(rms);
    if (this.window.length > this.windowFrames) this.window.shift();
    const threshold = Math.max(this.opts.absFloor, this.noise * this.opts.ratio);
    return rms > threshold;
  }
}

export class EnergyDetector implements VoiceDetector {
  readonly name = "energy" as const;
  private timer: ReturnType<typeof setInterval> | null = null;
  private src: MediaStreamAudioSourceNode | null = null;
  private analyser: AnalyserNode | null = null;

  constructor(
    private ctx: () => AudioContext,
    private opts: EnergyOptions = { frameMs: 20, ratio: 3, absFloor: 0.012 },
  ) {}

  async start(stream: MediaStream, onFrame: FrameCallback): Promise<void> {
    await this.stop();
    const ctx = this.ctx();
    if (ctx.state === "suspended") await ctx.resume().catch(() => {});
    this.src = ctx.createMediaStreamSource(stream);
    this.analyser = ctx.createAnalyser();
    this.analyser.fftSize = 1024;
    this.src.connect(this.analyser);
    const buf = new Float32Array(this.analyser.fftSize);
    const gate = new EnergyGate(this.opts);
    this.timer = setInterval(() => {
      if (!this.analyser) return;
      this.analyser.getFloatTimeDomainData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
      onFrame(gate.decide(Math.sqrt(sum / buf.length)), this.opts.frameMs);
    }, this.opts.frameMs);
  }

  async stop(): Promise<void> {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.src?.disconnect();
    this.src = null;
    this.analyser = null;
  }
}

// ---- silero (vad-web) ----------------------------------------------------------------------------

type MicVADType = import("@ricky0123/vad-web").MicVAD;

export class SileroDetector implements VoiceDetector {
  readonly name = "silero" as const;
  private vad: MicVADType | null = null;
  private stream: MediaStream | null = null;
  private onFrame: FrameCallback | null = null;
  private loading: Promise<MicVADType> | null = null;

  constructor(
    private ctx: () => AudioContext,
    private threshold = 0.5,
    private assetPath = "/vad/",
  ) {}

  /** Load the model ahead of time (call after the first tap so the first interrupt is fast). */
  warmUp(): Promise<unknown> {
    return this.ensure().catch(() => null);
  }

  private ensure(): Promise<MicVADType> {
    if (this.vad) return Promise.resolve(this.vad);
    if (!this.loading) {
      this.loading = (async () => {
        const { MicVAD } = await import("@ricky0123/vad-web");
        const vad = await MicVAD.new({
          model: "v5",
          baseAssetPath: this.assetPath,
          onnxWASMBasePath: this.assetPath,
          audioContext: this.ctx(),
          startOnLoad: false,
          // We feed our own stream; the VAD must never open or stop the mic itself.
          getStream: async () => this.requireStream(),
          pauseStream: async () => {},
          resumeStream: async () => this.requireStream(),
          positiveSpeechThreshold: this.threshold,
          negativeSpeechThreshold: Math.max(0.05, this.threshold - 0.15),
          onFrameProcessed: (probs) => {
            // v5 frames are 512 samples @ 16 kHz = 32 ms.
            this.onFrame?.(probs.isSpeech >= this.threshold, 32);
          },
        });
        this.vad = vad;
        return vad;
      })();
      this.loading.catch(() => (this.loading = null));
    }
    return this.loading;
  }

  private requireStream(): MediaStream {
    if (!this.stream) throw new Error("no mic stream");
    return this.stream;
  }

  async start(stream: MediaStream, onFrame: FrameCallback): Promise<void> {
    this.stream = stream;
    this.onFrame = onFrame;
    const vad = await this.ensure();
    await vad.start();
  }

  async stop(): Promise<void> {
    this.onFrame = null;
    if (this.vad?.listening) await this.vad.pause();
    this.stream = null;
  }
}
