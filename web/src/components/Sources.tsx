import type { CitationSource } from "../contracts/session";

/** Readable citations: the source's name, linking to the article (TODO 1.22). Raw passage ids are
 * never shown; citations the server couldn't resolve are summed up as "N more sources". */
export function Sources({ sources, citations }: { sources?: CitationSource[]; citations?: string[] }) {
  const list = sources ?? [];
  const named = list.filter((s) => s.source || s.url);
  const unnamed = list.length ? list.length - named.length : (citations?.length ?? 0);
  if (!named.length && !unnamed) return null;
  return (
    <span className="cites" data-testid="sources">
      {named.map((s) => {
        const label = s.source || hostOf(s.url) || "source";
        const tip = s.title ? `${label}: ${s.title}` : label;
        return s.url ? (
          <a
            key={s.url}
            className="cite"
            href={s.url}
            target="_blank"
            rel="noopener noreferrer"
            title={tip}
            aria-label={`Source: ${tip} (opens in a new tab)`}
          >
            <b>{label}</b>
            {s.title && <span className="cite-title">{s.title}</span>}
          </a>
        ) : (
          <span key={s.passage_id} className="cite" title={tip}>
            <b>{label}</b>
          </span>
        );
      })}
      {unnamed > 0 && (
        <span className="cite muted">
          {named.length ? `+${unnamed}` : unnamed} {unnamed === 1 ? "source" : "sources"}
        </span>
      )}
    </span>
  );
}

function hostOf(url?: string | null): string | null {
  if (!url) return null;
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return null;
  }
}
