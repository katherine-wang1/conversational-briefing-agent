import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { debugLog, LOG_CAP, type LogEntry } from "../lib/debugLog";
import { useStore } from "../lib/store";

const fmt = (t: number) => {
  const d = new Date(t);
  const p = (n: number, w = 2) => String(n).padStart(w, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}.${p(d.getMilliseconds(), 3)}`;
};

/** "Under the hood": a live, color-coded log of client and server events. Side panel on desktop
 * (≥ 900px), slide-up drawer with a toggle on phones (styles.css `.livelog`). */
export function LiveLog() {
  const entries = useStore(debugLog.entries);
  const [open, setOpen] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useLayoutEffect(() => {
    const el = listRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [entries, open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const last = entries[entries.length - 1];
  return (
    <>
      <button
        className={`livelog-toggle${open ? " on" : ""}`}
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-controls="livelog"
        data-testid="livelog-toggle"
      >
        <span className={`livelog-led k-${last?.kind ?? "info"}`} aria-hidden="true" />
        {open ? "Hide log" : "Log"}
      </button>
      <aside
        id="livelog"
        className={`livelog${open ? " open" : ""}`}
        data-testid="livelog"
        aria-label="What's happening"
      >
        <div className="livelog-head">
          <span className="livelog-title">Under the hood</span>
          <span className="small muted">
            {entries.length}
            {entries.length >= LOG_CAP ? " (latest)" : ""}
          </span>
          <button className="ghost small-btn" onClick={() => debugLog.clear()} data-testid="livelog-clear">
            Clear
          </button>
          <button className="ghost small-btn livelog-close" onClick={() => setOpen(false)} aria-label="Close the log">
            ✕
          </button>
        </div>
        <div
          className="livelog-list"
          ref={listRef}
          role="log"
          aria-live="off"
          onScroll={(e) => {
            const el = e.currentTarget;
            stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
          }}
        >
          {entries.length === 0 && (
            <div className="small muted livelog-empty">Nothing yet. Play the brief or ask something.</div>
          )}
          {entries.map((e: LogEntry) => (
            <div
              key={e.id}
              className={`log-row k-${e.kind}`}
              data-testid="log-row"
              data-kind={e.kind}
              data-from={e.from}
            >
              <time>{fmt(e.t)}</time>
              <span className="log-src" title={e.from === "server" ? "server" : "this device"}>
                {e.from === "server" ? "S" : "C"}
              </span>
              <span className="log-kind">{e.kind}</span>
              <span className="log-text">{e.text}</span>
            </div>
          ))}
        </div>
      </aside>
    </>
  );
}
