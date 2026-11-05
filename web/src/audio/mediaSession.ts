/**
 * Media Session wiring: AirPods squeeze / headphone button / lock-screen controls.
 *
 * iOS sends `pause` while our element is playing and `play` while it is paused, so:
 *   pause (brief or answer playing)  -> interrupt (open the mic)
 *   play  while listening            -> "done talking" (end the utterance now)
 *   play  otherwise                  -> interrupt (talk) if a session is live
 *   nexttrack                        -> next story
 */

export interface MediaHandlers {
  onPause(): void;
  onPlay(): void;
  onNext?(): void;
  onPrevious?(): void;
}

export function mediaSessionSupported(): boolean {
  return typeof navigator !== "undefined" && "mediaSession" in navigator;
}

export function installMediaSession(h: MediaHandlers): () => void {
  if (!mediaSessionSupported()) return () => {};
  const ms = navigator.mediaSession;
  const set = (action: MediaSessionAction, fn: (() => void) | null) => {
    try {
      ms.setActionHandler(action, fn);
    } catch {
      /* action unsupported on this platform */
    }
  };
  set("pause", h.onPause);
  set("stop", h.onPause);
  set("play", h.onPlay);
  set("nexttrack", h.onNext ?? null);
  set("previoustrack", h.onPrevious ?? null);
  // Seek handlers would replace next/previous on iOS lock screen; keep them off.
  set("seekbackward", null);
  set("seekforward", null);
  return () => {
    for (const a of ["pause", "stop", "play", "nexttrack", "previoustrack"] as MediaSessionAction[])
      set(a, null);
  };
}

export function setNowPlaying(title: string, artist = "Your briefing"): void {
  if (!mediaSessionSupported() || typeof MediaMetadata === "undefined") return;
  try {
    navigator.mediaSession.metadata = new MediaMetadata({
      title,
      artist,
      album: "Briefing",
      artwork: [{ src: "/icon-512.png", sizes: "512x512", type: "image/png" }],
    });
  } catch {
    /* ignore */
  }
}

export function setPlaybackState(state: MediaSessionPlaybackState): void {
  if (!mediaSessionSupported()) return;
  try {
    navigator.mediaSession.playbackState = state;
  } catch {
    /* ignore */
  }
}
