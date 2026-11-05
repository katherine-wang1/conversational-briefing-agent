import type { Me, Settings } from "../contracts/api";
import { browserSttSupported } from "../audio/browserStt";
import { mediaSessionSupported } from "../audio/mediaSession";
import { api } from "../lib/api";
import { auth, signOut } from "../lib/auth";
import { settings, type ClientSettings } from "../lib/settings";
import { useStore } from "../lib/store";

const VOICES = ["default", "alloy", "verse", "sage"];

export function SettingsScreen({ me }: { me: Me | null }) {
  const s = useStore(settings);
  const a = useStore(auth);

  /** Update locally; sync the server-side ones to PATCH /api/me (best effort). */
  const set = <K extends keyof ClientSettings>(k: K, v: ClientSettings[K]) => {
    settings.patch({ [k]: v } as Partial<ClientSettings>);
    const serverKeys: (keyof Settings)[] = ["brief_time", "voice", "stt_mode", "auto_resume", "headphones_mode"];
    if ((serverKeys as string[]).includes(k)) void api.patchMe({ [k]: v } as Partial<Settings>).catch(() => {});
  };

  return (
    <div className="page settings">
      <h1>Settings</h1>

      <label className="field">
        <span>Brief time</span>
        <input type="time" value={s.brief_time} onChange={(e) => set("brief_time", e.target.value)} />
      </label>

      <label className="field">
        <span>Voice</span>
        <select value={s.voice} onChange={(e) => set("voice", e.target.value)}>
          {VOICES.map((v) => (
            <option key={v}>{v}</option>
          ))}
        </select>
      </label>

      <fieldset className="field">
        <legend>Speech recognition</legend>
        <div className="seg">
          <button className={s.stt_mode === "browser" ? "on" : ""} onClick={() => set("stt_mode", "browser")}>
            Browser
          </button>
          <button className={s.stt_mode === "server" ? "on" : ""} onClick={() => set("stt_mode", "server")}>
            Server
          </button>
        </div>
        {!browserSttSupported() && <span className="small muted">This browser has no Web Speech API; server STT is used.</span>}
      </fieldset>

      <label className="check">
        <input type="checkbox" checked={s.auto_resume} onChange={(e) => set("auto_resume", e.target.checked)} />
        <span>Go back to the brief automatically after an answer</span>
      </label>

      <label className="check">
        <input type="checkbox" checked={s.headphones_mode} onChange={(e) => set("headphones_mode", e.target.checked)} />
        <span>
          Headphones mode (experimental): keep the mic open and interrupt by just talking. Use with headphones only.
        </span>
      </label>

      <details className="field">
        <summary>Advanced (voice detection)</summary>
        <label className="field">
          <span>Voice detector</span>
          <select value={s.vad_engine} onChange={(e) => set("vad_engine", e.target.value as ClientSettings["vad_engine"])}>
            <option value="silero">Silero (default)</option>
            <option value="energy">Simple energy</option>
          </select>
        </label>
        <label className="field">
          <span>End of speech after silence: {s.silence_ms} ms</span>
          <input
            type="range"
            min={300}
            max={2000}
            step={50}
            value={s.silence_ms}
            onChange={(e) => set("silence_ms", Number(e.target.value))}
          />
        </label>
        <label className="field">
          <span>Barge-in onset: {s.barge_in_onset_ms} ms</span>
          <input
            type="range"
            min={100}
            max={800}
            step={10}
            value={s.barge_in_onset_ms}
            onChange={(e) => set("barge_in_onset_ms", Number(e.target.value))}
          />
        </label>
        <label className="field">
          <span>Interrupt on</span>
          <select value={s.tap_event} onChange={(e) => set("tap_event", e.target.value as ClientSettings["tap_event"])}>
            <option value="pointerdown">Touch down (fastest)</option>
            <option value="click">Tap release</option>
          </select>
        </label>
        <p className="small muted">
          Media Session (AirPods controls): {mediaSessionSupported() ? "available" : "not available"} · Secure context:{" "}
          {String(window.isSecureContext)}
        </p>
      </details>

      <div className="field">
        <span className="small muted">
          Signed in as {me?.email ?? a.email} {a.via === "dev" && "(dev bypass)"}
        </span>
        <button className="wide" onClick={() => void signOut()}>
          Sign out
        </button>
      </div>
    </div>
  );
}
