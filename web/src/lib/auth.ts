import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { config, supabaseConfigured } from "./config";
import { Store } from "./store";

export interface AuthState {
  status: "loading" | "signed_out" | "signed_in";
  token: string | null;
  email: string | null;
  via: "supabase" | "dev" | null;
}

const DEV_KEY = "briefing.devAuth";

export const auth = new Store<AuthState>({ status: "loading", token: null, email: null, via: null });

let supabase: SupabaseClient | null = null;

export function getSupabase(): SupabaseClient | null {
  if (!supabaseConfigured) return null;
  if (!supabase) supabase = createClient(config.supabaseUrl, config.supabaseAnonKey);
  return supabase;
}

export async function initAuth(): Promise<void> {
  const dev = safeGet(DEV_KEY);
  if (dev && config.devBypass) {
    auth.set({ status: "signed_in", token: config.devToken, email: dev, via: "dev" });
    return;
  }
  const sb = getSupabase();
  if (!sb) {
    auth.set({ status: "signed_out", token: null, email: null, via: null });
    return;
  }
  const { data } = await sb.auth.getSession();
  applySession(data.session?.access_token ?? null, data.session?.user.email ?? null);
  sb.auth.onAuthStateChange((_event, session) => {
    if (auth.get().via === "dev") return;
    applySession(session?.access_token ?? null, session?.user.email ?? null);
  });
}

function applySession(token: string | null, email: string | null) {
  auth.set(
    token
      ? { status: "signed_in", token, email, via: "supabase" }
      : { status: "signed_out", token: null, email: null, via: null },
  );
}

export async function signInWithMagicLink(email: string): Promise<void> {
  const sb = getSupabase();
  if (!sb) throw new Error("Supabase is not configured (VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY).");
  const { error } = await sb.auth.signInWithOtp({
    email,
    options: { emailRedirectTo: window.location.origin, shouldCreateUser: false }, // invite-gated
  });
  if (error) throw error;
}

export async function signInWithGoogle(): Promise<void> {
  const sb = getSupabase();
  if (!sb) throw new Error("Supabase is not configured.");
  const { error } = await sb.auth.signInWithOAuth({
    provider: "google",
    options: { redirectTo: window.location.origin },
  });
  if (error) throw error;
}

export function signInDev(email: string): void {
  safeSet(DEV_KEY, email || "dev@example.com");
  auth.set({ status: "signed_in", token: config.devToken, email: email || "dev@example.com", via: "dev" });
}

export async function signOut(): Promise<void> {
  safeSet(DEV_KEY, null);
  await getSupabase()?.auth.signOut();
  auth.set({ status: "signed_out", token: null, email: null, via: null });
}

function safeGet(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSet(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* private mode */
  }
}
