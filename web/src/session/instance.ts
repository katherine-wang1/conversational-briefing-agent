import { api, resolveWsUrl } from "../lib/api";
import { auth } from "../lib/auth";
import { settings } from "../lib/settings";
import { Conversation } from "./conversation";

/** One live conversation per page, kept alive across tab switches (audio keeps playing). */
let conv: Conversation | null = null;
let unsubscribe: (() => void) | null = null;

export function getConversation(): Conversation {
  if (!conv) {
    conv = new Conversation({
      createSession: (briefId) => api.createSession(briefId),
      wsUrl: (u) => resolveWsUrl(u, auth.get().token),
      settings: () => settings.get(),
    });
    // Speed and live log apply immediately when changed in Settings.
    const c = conv;
    let prev = settings.get();
    unsubscribe = settings.subscribe(() => {
      const cur = settings.get();
      if (cur.playback_rate !== prev.playback_rate || cur.show_debug !== prev.show_debug) {
        c.applySettings();
      }
      prev = cur;
    });
    // Handle for the debug panel and the Playwright smoke test.
    (window as unknown as { __conv?: Conversation }).__conv = conv;
  }
  return conv;
}

export function resetConversation(): Conversation {
  conv?.dispose();
  unsubscribe?.();
  unsubscribe = null;
  conv = null;
  return getConversation();
}
