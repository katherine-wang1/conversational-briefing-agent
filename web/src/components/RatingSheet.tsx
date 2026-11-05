import { useState } from "react";
import type { RatingRequest } from "../contracts/api";
import type { BriefStory } from "../contracts/brief";
import { api } from "../lib/api";

function Scale({ value, onChange, label }: { value: number | null; onChange: (v: number) => void; label: string }) {
  return (
    <div className="scale" role="radiogroup" aria-label={label}>
      {[1, 2, 3, 4, 5].map((n) => (
        <button key={n} role="radio" aria-checked={value === n} className={value === n ? "on" : ""} onClick={() => onChange(n)}>
          {n}
        </button>
      ))}
    </div>
  );
}

/** Post-session rating (~30 s): usefulness, per-story relevance, learned-new, length fit, follow-ups. */
export function RatingSheet({
  sessionId,
  stories,
  hadFollowups,
  onClose,
}: {
  sessionId: string;
  stories: BriefStory[];
  hadFollowups: boolean;
  onClose: () => void;
}) {
  const [usefulness, setUsefulness] = useState<number | null>(null);
  const [perStory, setPerStory] = useState<Record<string, number>>({});
  const [learned, setLearned] = useState<boolean | null>(null);
  const [lengthFit, setLengthFit] = useState<RatingRequest["length_fit"] | null>(null);
  const [followup, setFollowup] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    try {
      await api.rate({
        session_id: sessionId,
        usefulness: usefulness!,
        per_story: perStory,
        learned_new: learned ?? false,
        length_fit: lengthFit ?? "about_right",
        followup_useful: hadFollowups ? followup : null,
      });
      onClose();
    } catch (err) {
      setError(String(err));
      setBusy(false);
    }
  };

  return (
    <div className="sheet-backdrop">
      <div className="sheet" role="dialog" aria-label="Rate this session" data-testid="rating-sheet">
        <h2>How was today's brief?</h2>
        <div className="field">
          <span>Useful overall</span>
          <Scale value={usefulness} onChange={setUsefulness} label="Usefulness" />
        </div>
        {stories.length > 0 && (
          <div className="field">
            <span>Relevant to you?</span>
            {stories.map((st) => (
              <div key={st.story_ref} className="story-rate">
                <div className="small">{st.headline}</div>
                <Scale
                  value={perStory[st.story_ref] ?? null}
                  onChange={(v) => setPerStory((p) => ({ ...p, [st.story_ref]: v }))}
                  label={st.headline}
                />
              </div>
            ))}
          </div>
        )}
        <div className="field">
          <span>Learned something new?</span>
          <div className="seg">
            <button className={learned === true ? "on" : ""} onClick={() => setLearned(true)}>
              Yes
            </button>
            <button className={learned === false ? "on" : ""} onClick={() => setLearned(false)}>
              No
            </button>
          </div>
        </div>
        <div className="field">
          <span>Length</span>
          <div className="seg">
            {(["too_short", "about_right", "too_long"] as const).map((v) => (
              <button key={v} className={lengthFit === v ? "on" : ""} onClick={() => setLengthFit(v)}>
                {v.replace("_", " ")}
              </button>
            ))}
          </div>
        </div>
        {hadFollowups && (
          <div className="field">
            <span>Were the answers to your questions useful?</span>
            <Scale value={followup} onChange={setFollowup} label="Follow-up usefulness" />
          </div>
        )}
        {error && <div className="banner error">{error}</div>}
        <div className="row">
          <button className="grow" onClick={onClose}>
            Skip
          </button>
          <button className="primary grow" disabled={usefulness === null || busy} onClick={() => void submit()}>
            Submit
          </button>
        </div>
      </div>
    </div>
  );
}
