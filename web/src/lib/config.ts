// Build-time configuration (Vite env). See web/README.md.
const env = import.meta.env;

export const config = {
  /** API base. Empty = same origin (the Vite dev server proxies /api, /ws, /audio). */
  apiBase: (env.VITE_API_BASE as string | undefined) ?? "",
  supabaseUrl: (env.VITE_SUPABASE_URL as string | undefined) ?? "",
  supabaseAnonKey: (env.VITE_SUPABASE_ANON_KEY as string | undefined) ?? "",
  /** Dev bypass: on by default in `vite dev`, off in builds unless VITE_DEV_BYPASS=true. */
  devBypass: env.VITE_DEV_BYPASS ? env.VITE_DEV_BYPASS === "true" : env.DEV,
  /** Bearer token sent when signed in through the dev bypass. */
  devToken: (env.VITE_DEV_TOKEN as string | undefined) ?? "dev",
};

export const supabaseConfigured = Boolean(config.supabaseUrl && config.supabaseAnonKey);
