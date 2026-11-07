import type { AudioManifest, BriefScript } from "../contracts/brief";

/** Seconds of brief audio from segment `from` on, at playback `rate` (TODO 1.25: from the
 * manifest's real segment durations; the script's estimate only when there is no manifest). */
export function briefSeconds(
  manifest: AudioManifest | null | undefined,
  script: BriefScript | null | undefined,
  rate = 1,
  from = 0,
): number | null {
  const r = rate > 0 ? rate : 1;
  const segs = manifest?.segments ?? [];
  const ms = segs.slice(Math.max(0, from)).reduce((a, s) => a + (Number(s.duration_ms) || 0), 0);
  if (ms > 0) return ms / 1000 / r;
  if (script?.est_duration_s)
    return (script.est_duration_s * (segs.length ? (segs.length - from) / segs.length : 1)) / r;
  return null;
}

/** "under a minute", "~2.5 min", "~12 min". */
export function formatDuration(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return "";
  const min = seconds / 60;
  if (min < 0.75) return "<1 min";
  if (min < 10) {
    const half = Math.round(min * 2) / 2;
    return `~${Number.isInteger(half) ? half : half.toFixed(1)} min`;
  }
  return `~${Math.round(min)} min`;
}
