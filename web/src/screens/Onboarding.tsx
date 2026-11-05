import { useState } from "react";
import type { OnboardingRequest } from "../contracts/api";
import { api } from "../lib/api";

const guessTz = () => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "America/Los_Angeles";
  } catch {
    return "America/Los_Angeles";
  }
};

export function Onboarding({ onDone }: { onDone: () => void }) {
  const [form, setForm] = useState<OnboardingRequest>({
    interests_text: "",
    length_pref: "medium",
    depth_pref: "standard",
    brief_time: "07:30",
    timezone: guessTz(),
    research_consent: false,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = <K extends keyof OnboardingRequest>(k: K, v: OnboardingRequest[K]) =>
    setForm((f) => ({ ...f, [k]: v }));

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.onboarding(form);
      onDone();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page">
      <h1>Welcome</h1>
      <label className="field">
        <span>What do you want to keep up with?</span>
        <textarea
          rows={5}
          value={form.interests_text}
          onChange={(e) => set("interests_text", e.target.value)}
          placeholder="e.g. agent memory and long-context research, grid batteries, humanoid robots, who's funding AI chips…"
        />
      </label>

      <fieldset className="field">
        <legend>Brief length</legend>
        <div className="seg">
          {(["short", "medium", "long"] as const).map((v) => (
            <button key={v} className={form.length_pref === v ? "on" : ""} onClick={() => set("length_pref", v)}>
              {v === "short" ? "~2 min" : v === "medium" ? "~4 min" : "~7 min"}
            </button>
          ))}
        </div>
      </fieldset>

      <fieldset className="field">
        <legend>Depth</legend>
        <div className="seg">
          {(["light", "standard", "deep"] as const).map((v) => (
            <button key={v} className={form.depth_pref === v ? "on" : ""} onClick={() => set("depth_pref", v)}>
              {v}
            </button>
          ))}
        </div>
      </fieldset>

      <div className="row">
        <label className="field grow">
          <span>Brief time</span>
          <input type="time" value={form.brief_time} onChange={(e) => set("brief_time", e.target.value)} />
        </label>
        <label className="field grow">
          <span>Timezone</span>
          <input value={form.timezone} onChange={(e) => set("timezone", e.target.value)} />
        </label>
      </div>

      <label className="check">
        <input
          type="checkbox"
          checked={form.research_consent}
          onChange={(e) => set("research_consent", e.target.checked)}
        />
        <span>
          Use my anonymised sessions and ratings for the course research project (optional; you can change this
          later).
        </span>
      </label>

      <button className="primary wide" disabled={busy || !form.interests_text.trim()} onClick={() => void submit()}>
        {busy ? "Saving…" : "Start"}
      </button>
      {error && <div className="banner error">{error}</div>}
    </div>
  );
}
