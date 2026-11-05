import { useEffect, useState } from "react";
import type { OpenQuestion } from "../contracts/api";
import { api } from "../lib/api";

export function Questions() {
  const [items, setItems] = useState<OpenQuestion[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.questions().then(setItems, (e) => setError(String(e)));
  }, []);

  const dismiss = async (id: string) => {
    try {
      const q = await api.dismissQuestion(id);
      setItems((xs) => xs?.map((x) => (x.id === id ? q : x)) ?? null);
    } catch (e) {
      setError(String(e));
    }
  };

  const visible = items?.filter((q) => q.status !== "dismissed") ?? [];

  return (
    <div className="page">
      <h1>Open questions</h1>
      <p className="muted small">Things you asked that I'm still looking into. Answers come in your next brief.</p>
      {error && <div className="banner error">{error}</div>}
      {items && visible.length === 0 && <p className="muted">No open questions.</p>}
      {visible.map((q) => (
        <div key={q.id} className="card">
          <div>{q.question}</div>
          <div className="small muted">
            <span className={`status ${q.status}`}>{q.status}</span> · asked {new Date(q.created_at).toLocaleDateString()}
          </div>
          {q.answer && <div className="answer">{q.answer.text}</div>}
          {q.status !== "answered" && (
            <button className="ghost small" onClick={() => void dismiss(q.id)}>
              Dismiss
            </button>
          )}
        </div>
      ))}
    </div>
  );
}
