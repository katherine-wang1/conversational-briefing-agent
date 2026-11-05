import { api, resolveWsUrl } from "../lib/api";
import { auth } from "../lib/auth";
import { settings } from "../lib/settings";
import { Conversation } from "./conversation";

/** One live conversation per page, kept alive across tab switches (audio keeps playing). */
let conv: Conversation | null = null;

export function getConversation(): Conversation {
  if (!conv) {
    conv = new Conversation({
      createSession: (briefId) => api.createSession(briefId),
      wsUrl: (u) => resolveWsUrl(u, auth.get().token),
      settings: () => settings.get(),
    });
    // Handle for the debug panel and the Playwright smoke test.
    (window as unknown as { __conv?: Conversation }).__conv = conv;
  }
  return conv;
}

export function resetConversation(): Conversation {
  conv?.dispose();
  conv = null;
  return getConversation();
}
