import { useEffect, useState } from "react";
import type { HistoryItem } from "../contracts/api";
import { api } from "../lib/api";

export function History() {
  const [items, setItems] = useState<HistoryItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    api.history(14).then(setItems, (e) => setError(String(e)));
  }, []);

  // The backend lists briefs and sessions; a session's turns come from its transcript, on demand.
  const toggle = (id: string) => {
    const next = open === id ? null : id;
    setOpen(next);
    const it = items?.find((x) => x.brief_id === next);
    if (!it || it.turns || !it.session_id) return;
    api.transcript(it.session_id).then(
      (turns) => setItems((xs) => xs?.map((x) => (x.brief_id === id ? { ...x, turns } : x)) ?? null),
      (e) => setError(String(e)),
    );
  };

  return (
    <div className="page">
      <h1>History</h1>
      {error && <div className="banner error">{error}</div>}
      {items?.length === 0 && <p className="muted">No past briefs yet.</p>}
      {items?.map((it) => (
        <div key={it.brief_id} className="card">
          <button className="card-head" onClick={() => toggle(it.brief_id)}>
            <span className="title">{it.local_date}</span>
            <span className="muted small">{it.headlines.length} stories</span>
          </button>
          <ul className="small">
            {it.headlines.map((h) => (
              <li key={h}>{h}</li>
            ))}
          </ul>
          {open === it.brief_id && it.turns?.length === 0 && <p className="small muted">No conversation.</p>}
          {open === it.brief_id && !!it.turns?.length && (
            <div className="transcript static">
              {it.turns.map((t, i) => (
                <div key={i} className={`line ${t.role}`}>
                  <span>{t.text}</span>
                  {t.citations?.map((c) => {
                    const url = it.citation_urls?.[c];
                    return url ? (
                      <a key={c} className="cite" href={url} target="_blank" rel="noreferrer">
                        {c}
                      </a>
                    ) : (
                      <span key={c} className="cite">
                        {c}
                      </span>
                    );
                  })}
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
