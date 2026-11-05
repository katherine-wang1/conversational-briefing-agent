/** Microphone helpers. The mic is opened per utterance (stage 1) so iOS returns to the
 * playback-only audio session (full volume, AirPods A2DP) as soon as the user is done talking. */

export const MIC_CONSTRAINTS: MediaStreamConstraints = {
  audio: {
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
    channelCount: 1,
  },
};

export class MicError extends Error {
  constructor(
    public kind: "denied" | "unavailable" | "insecure",
    message: string,
  ) {
    super(message);
  }
}

export async function openMic(): Promise<MediaStream> {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new MicError(
      window.isSecureContext ? "unavailable" : "insecure",
      window.isSecureContext
        ? "This browser has no microphone API."
        : "The microphone needs HTTPS (or localhost).",
    );
  }
  try {
    return await navigator.mediaDevices.getUserMedia(MIC_CONSTRAINTS);
  } catch (err) {
    const name = err instanceof DOMException ? err.name : "";
    if (name === "NotAllowedError" || name === "SecurityError")
      throw new MicError("denied", "Microphone permission was denied. Enable it in Settings > Safari.");
    throw new MicError("unavailable", `Could not open the microphone (${name || String(err)}).`);
  }
}

export function closeMic(stream: MediaStream | null): void {
  stream?.getTracks().forEach((t) => t.stop());
}

/** Records the utterance for server STT. Picks webm/opus where supported, mp4/aac on Safari. */
export class UtteranceRecorder {
  private rec: MediaRecorder | null = null;
  private chunks: Blob[] = [];
  mimeType = "";

  static supported(): boolean {
    return typeof MediaRecorder !== "undefined";
  }

  start(stream: MediaStream): void {
    const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
    const mime = candidates.find((m) => MediaRecorder.isTypeSupported?.(m)) ?? "";
    this.chunks = [];
    this.rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    this.mimeType = this.rec.mimeType || mime || "audio/webm";
    this.rec.ondataavailable = (e) => {
      if (e.data.size) this.chunks.push(e.data);
    };
    this.rec.start(250);
  }

  /** Stop and return the recording (null if nothing was recorded). */
  stop(): Promise<Blob | null> {
    const rec = this.rec;
    this.rec = null;
    if (!rec || rec.state === "inactive") return Promise.resolve(null);
    return new Promise((resolve) => {
      rec.onstop = () => {
        const blob = this.chunks.length ? new Blob(this.chunks, { type: this.mimeType }) : null;
        this.chunks = [];
        resolve(blob);
      };
      rec.stop();
    });
  }

  cancel(): void {
    void this.stop();
  }
}

/** "audio/webm;codecs=opus" -> "webm/opus", "audio/mp4" -> "mp4/aac". */
export function protocolFormat(mime: string): string {
  const m = mime.toLowerCase();
  if (m.includes("webm")) return "webm/opus";
  if (m.includes("ogg")) return "ogg/opus";
  if (m.includes("mp4") || m.includes("aac")) return "mp4/aac";
  return m.replace(/^audio\//, "");
}

export async function blobToB64(blob: Blob): Promise<string> {
  const buf = new Uint8Array(await blob.arrayBuffer());
  let bin = "";
  const step = 0x8000;
  for (let i = 0; i < buf.length; i += step) bin += String.fromCharCode(...buf.subarray(i, i + step));
  return btoa(bin);
}
