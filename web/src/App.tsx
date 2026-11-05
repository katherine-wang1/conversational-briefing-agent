import { useCallback, useEffect, useState } from "react";
import type { Me } from "./contracts/api";
import { api } from "./lib/api";
import { auth } from "./lib/auth";
import { navigate, useRoute, type Route } from "./lib/router";
import { applyServerSettings } from "./lib/settings";
import { useStore } from "./lib/store";
import { History } from "./screens/History";
import { Login } from "./screens/Login";
import { Memory } from "./screens/Memory";
import { Onboarding } from "./screens/Onboarding";
import { Questions } from "./screens/Questions";
import { SettingsScreen } from "./screens/Settings";
import { Today } from "./screens/Today";

const TABS: { route: Route; label: string }[] = [
  { route: "today", label: "Today" },
  { route: "history", label: "History" },
  { route: "memory", label: "Memory" },
  { route: "questions", label: "Questions" },
  { route: "settings", label: "Settings" },
];

export function App() {
  const a = useStore(auth);
  const route = useRoute();
  const [me, setMe] = useState<Me | null>(null);
  const [meError, setMeError] = useState<string | null>(null);
  const [openQuestions, setOpenQuestions] = useState(0);

  const refreshMe = useCallback(async () => {
    try {
      const m = await api.me();
      setMe(m);
      setMeError(null);
      applyServerSettings({
        stt_mode: m.settings.stt_mode,
        auto_resume: m.settings.auto_resume,
        headphones_mode: m.settings.headphones_mode,
        voice: m.settings.voice,
        brief_time: m.settings.brief_time,
      });
      if (!m.onboarded) navigate("onboarding");
    } catch (err) {
      setMeError(String(err));
    }
  }, []);

  useEffect(() => {
    if (a.status === "signed_in") void refreshMe();
  }, [a.status, refreshMe]);

  useEffect(() => {
    if (a.status !== "signed_in") return;
    api
      .questions()
      .then((qs) => setOpenQuestions(qs.filter((q) => q.status === "open" || q.status === "researching").length))
      .catch(() => {});
  }, [a.status, route]);

  if (a.status === "loading") return <div className="center muted">Loading…</div>;
  if (a.status === "signed_out") return <Login />;

  return (
    <div className="app">
      <main className="screen">
        {meError && !me && <div className="banner error">Can't reach the server: {meError}</div>}
        {route === "onboarding" && <Onboarding onDone={() => void refreshMe().then(() => navigate("today"))} />}
        {route === "today" && <Today openQuestions={openQuestions} />}
        {route === "history" && <History />}
        {route === "memory" && <Memory />}
        {route === "questions" && <Questions />}
        {route === "settings" && <SettingsScreen me={me} />}
      </main>
      {route !== "onboarding" && (
        <nav className="tabs">
          {TABS.map((t) => (
            <a key={t.route} href={`#/${t.route}`} className={route === t.route ? "active" : ""}>
              {t.label}
              {t.route === "questions" && openQuestions > 0 && <span className="dot">{openQuestions}</span>}
            </a>
          ))}
        </nav>
      )}
    </div>
  );
}
