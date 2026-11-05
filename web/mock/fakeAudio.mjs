// Fake "TTS": deterministic babble-like MP3 (pitched syllables), so playback, preloading and
// interruption can be exercised without any TTS provider. Not speech; never presented as such.
import { Mp3Encoder } from "@breezystack/lamejs";

const RATE = 22050;
const cache = new Map();

function seeded(str) {
  let h = 2166136261;
  for (const c of str) h = Math.imul(h ^ c.charCodeAt(0), 16777619);
  return () => {
    h = Math.imul(h ^ (h >>> 15), 2246822507);
    h = Math.imul(h ^ (h >>> 13), 3266489909);
    h ^= h >>> 16;
    return (h >>> 0) / 4294967296;
  };
}

/** Duration the mock assigns to a sentence (≈ 14 chars/s speech rate, scaled for tests). */
export function durationFor(text, scale = 1) {
  return Math.max(400, Math.round(text.length * 70 * scale));
}

/**
 * MP3 bytes for `key` lasting `ms`. `voice` shifts pitch: brief vs answer audio sound different.
 */
export function fakeMp3(key, ms, voice = "brief") {
  const ck = `${key}|${ms}|${voice}`;
  if (cache.has(ck)) return cache.get(ck);
  const rnd = seeded(ck);
  const n = Math.round((RATE * ms) / 1000);
  const pcm = new Int16Array(n);
  const base = voice === "answer" ? 220 : 160;
  const syl = Math.round(RATE * 0.17);
  let phase = 0;
  for (let s = 0; s < n; s += syl) {
    const f0 = base + rnd() * 80;
    const silent = rnd() < 0.12;
    for (let i = 0; i < syl && s + i < n; i++) {
      const env = Math.sin((Math.PI * i) / syl);
      phase += (2 * Math.PI * f0) / RATE;
      const v = silent ? 0 : env * (Math.sin(phase) + 0.4 * Math.sin(2 * phase) + 0.2 * Math.sin(3 * phase));
      pcm[s + i] = Math.round(v * 6000);
    }
  }
  const enc = new Mp3Encoder(1, RATE, 48);
  const parts = [];
  for (let i = 0; i < n; i += 1152) {
    const b = enc.encodeBuffer(pcm.subarray(i, i + 1152));
    if (b.length) parts.push(Buffer.from(b));
  }
  const end = enc.flush();
  if (end.length) parts.push(Buffer.from(end));
  const buf = Buffer.concat(parts);
  cache.set(ck, buf);
  return buf;
}
