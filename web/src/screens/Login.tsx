import { useState } from "react";
import { signInDev, signInWithGoogle, signInWithMagicLink } from "../lib/auth";
import { config, supabaseConfigured } from "../lib/config";

export function Login() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const magic = async () => {
    setBusy(true);
    setError(null);
    try {
      await signInWithMagicLink(email.trim());
      setSent(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <h1>Briefing</h1>
      <p className="muted">Your daily news brief, out loud. Interrupt any time to ask.</p>

      {supabaseConfigured ? (
        <>
          <label className="field">
            <span>Email</span>
            <input
              type="email"
              autoComplete="email"
              inputMode="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
            />
          </label>
          <button className="primary wide" disabled={!email || busy} onClick={() => void magic()}>
            {sent ? "Link sent — check your email" : "Email me a sign-in link"}
          </button>
          <button className="wide" onClick={() => void signInWithGoogle().catch((e) => setError(String(e)))}>
            Continue with Google
          </button>
          <p className="muted small">Invite only. Ask Kate for an invite.</p>
        </>
      ) : (
        <p className="muted small">Supabase isn't configured (VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY).</p>
      )}

      {config.devBypass && (
        <div className="devbox">
          <p className="small muted">Development</p>
          <button className="wide" data-testid="dev-login" onClick={() => signInDev(email.trim() || "dev@example.com")}>
            Dev sign-in (bypass)
          </button>
        </div>
      )}
      {error && <div className="banner error">{error}</div>}
    </div>
  );
}
