import { useEffect, useState } from "react";
import type { Interest } from "../contracts/brief";
import { api } from "../lib/api";

export function Memory() {
  const [items, setItems] = useState<Interest[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  useEffect(() => {
    api.interests().then(setItems, (e) => setError(String(e)));
  }, []);

  const replace = (it: Interest) => setItems((xs) => xs?.map((x) => (x.id === it.id ? it : x)) ?? null);
  const run = async (fn: () => Promise<void>) => {
    try {
      setError(null);
      await fn();
    } catch (e) {
      setError(String(e));
    }
  };

  const sorted = [...(items ?? [])].sort(
    (a, b) => Number(b.explicit_pin) - Number(a.explicit_pin) || b.weight - a.weight,
  );

  return (
    <div className="page">
      <h1>What I know you follow</h1>
      <p className="muted small">Pinned items always count. Delete anything that's wrong.</p>
      {error && <div className="banner error">{error}</div>}
      {items?.length === 0 && <p className="muted">Nothing yet.</p>}
      <ul className="interests">
        {sorted.map((it) => (
          <li key={it.id} className="interest">
            {editing === it.id ? (
              <form
                className="grow row"
                onSubmit={(e) => {
                  e.preventDefault();
                  void run(async () => {
                    replace(await api.patchInterest(it.id, { label: draft }));
                    setEditing(null);
                  });
                }}
              >
                <input className="grow" autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} />
                <button type="submit">Save</button>
              </form>
            ) : (
              <div className="grow">
                <div>
                  {it.explicit_pin && "📌 "}
                  {it.label}
                </div>
                <div className="small muted">
                  {it.subtopic} · weight {it.weight.toFixed(2)} · {it.source}
                </div>
                <input
                  type="range"
                  min={0}
                  max={2}
                  step={0.1}
                  value={it.weight}
                  aria-label={`Weight for ${it.label}`}
                  onChange={(e) => replace({ ...it, weight: Number(e.target.value) })}
                  onPointerUp={(e) =>
                    void run(async () => {
                      replace(await api.patchInterest(it.id, { weight: Number((e.target as HTMLInputElement).value) }));
                    })
                  }
                />
              </div>
            )}
            <div className="actions">
              <button
                onClick={() =>
                  void run(async () => replace(await api.patchInterest(it.id, { explicit_pin: !it.explicit_pin })))
                }
              >
                {it.explicit_pin ? "Unpin" : "Pin"}
              </button>
              <button
                onClick={() => {
                  setEditing(it.id);
                  setDraft(it.label);
                }}
              >
                Edit
              </button>
              <button
                className="danger"
                onClick={() =>
                  void run(async () => {
                    await api.deleteInterest(it.id);
                    setItems((xs) => xs?.filter((x) => x.id !== it.id) ?? null);
                  })
                }
              >
                Delete
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
