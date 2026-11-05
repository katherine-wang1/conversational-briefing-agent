import { useEffect, useRef, useState } from "react";
import type { TodayBrief } from "../contracts/api";
import { RatingSheet } from "../components/RatingSheet";
import { api } from "../lib/api";
import { navigate } from "../lib/router";
import { settings } from "../lib/settings";
import { useStore } from "../lib/store";
import type { Phase } from "../session/conversation";
import { getConversation, resetConversation } from "../session/instance";

const TALK_LABEL: Record<Phase, string> = {
  idle: "",
  ready: "Play today's brief",
  playing: "Tap anywhere here to interrupt",
  listening: "Listening…",
  thinking: "Thinking…",
  answering: "Tap to interrupt",
  paused: "Tap to ask something",
  open_floor: "Tap to ask something",
  ended: "Session ended",
};

export function Today({ openQuestions }: { openQuestions: number }) {
  const [conv, setConv] = useState(getConversation);
  const s = useStore(conv.state);
  const tapEvent = useStore(settings).tap_event;
  const [brief, setBrief] = useState<TodayBrief | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [typing, setTyping] = useState(false);
  const [typed, setTyped] = useState("");
  const transcriptRef = useRef<HTMLDivElement>(null);

  // Load today's brief; poll while the chief-of-staff run is still producing it.
  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try {
        const b = await api.today();
        if (stop) return;
        setBrief(b);
        setLoadError(null);
        if (b.status === "ready" && b.manifest) {
          if (conv.state.get().phase === "idle") conv.load(b.script, b.manifest);
        } else if (b.status === "pending") timer = setTimeout(load, 4000);
      } catch (err) {
        if (!stop) setLoadError(String(err));
      }
    };
    void load();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [conv]);

  useEffect(() => {
    transcriptRef.current?.scrollTo({ top: transcriptRef.current.scrollHeight, behavior: "smooth" });
  }, [s.transcript.length, s.interim]);

  const script = brief?.script ?? null;
  const story = script?.stories.find((st) => st.story_ref === s.storyRef) ?? null;
  const storyNo = story ? script!.stories.indexOf(story) + 1 : null;
  const sentence = script?.sentences.find((x) => x.sid === brief?.manifest?.segments[s.segmentIndex]?.sid);
  const badge = Math.max(openQuestions, 0) + s.openQuestions;

  // Play must run in an activation-triggering event (click / touchend) for iOS to allow audio.
  // Interrupt runs on pointerdown by default (faster); pausing doesn't need user activation.
  const onTalk = (ev: "down" | "click") => {
    if (s.phase === "ready") {
      if (ev === "click") void conv.play(brief!.brief_id).catch((e) => alert(String(e)));
      return;
    }
    if ((ev === "down") === (tapEvent === "pointerdown")) conv.interrupt("tap");
  };

  return (
    <div className="today">
      <header className="today-head">
        <div>
          <div className="small muted">{script?.local_date ?? "Today"}</div>
          <div className="title">
            {script ? `${script.stories.length} stories · ~${Math.round(script.est_duration_s / 60)} min` : "Your brief"}
          </div>
        </div>
        <button className="badge-btn" onClick={() => navigate("questions")} aria-label="Open questions">
          ?{badge > 0 && <span className="dot" data-testid="oq-badge">{badge}</span>}
        </button>
        {s.phase !== "idle" && s.phase !== "ready" && s.phase !== "ended" && (
          <button className="ghost" onClick={() => conv.end()} data-testid="end">
            End
          </button>
        )}
      </header>

      {loadError && <div className="banner error">Couldn't load today's brief: {loadError}</div>}
      {brief?.status === "pending" && <div className="banner">Preparing your brief… this can take a minute.</div>}
      {brief?.status === "failed" && <div className="banner error">Today's brief failed to build.</div>}

      <section className="story-card" data-testid="story-card">
        {story ? (
          <>
            <div className="small muted">
              Story {storyNo} of {script!.stories.length} · <span className="tag">{story.why_tag}</span>
            </div>
            <div className="headline">{story.headline}</div>
          </>
        ) : (
          <div className="headline muted">
            {s.phase === "open_floor"
              ? "That's the brief. Tap below to ask about anything."
              : s.phase === "ready" || s.phase === "idle"
                ? script?.stories.map((st) => st.headline).slice(0, 1)[0] ?? "Your brief"
                : "Today's brief"}
          </div>
        )}
        {sentence && s.phase === "playing" && <div className="sentence">{sentence.text}</div>}
      </section>

      <section className="transcript" ref={transcriptRef} data-testid="transcript">
        {s.transcript.map((l) => (
          <div key={l.id} className={`line ${l.role}${l.pending ? " pending" : ""}`}>
            <span>{l.text}</span>
            {l.citations.length > 0 && (
              <span className="cites">
                {l.citations.map((c) => (
                  <span key={c} className="cite">
                    {c}
                  </span>
                ))}
              </span>
            )}
          </div>
        ))}
        {s.phase === "listening" && s.interim && <div className="line user interim">{s.interim}</div>}
      </section>

      {(s.notice || s.error) && (
        <div className={`banner ${s.error ? "error" : ""}`} data-testid="notice">
          {s.error ?? s.notice}
        </div>
      )}

      <div className="controls">
        <button onClick={() => conv.skip()} disabled={!["playing", "paused", "answering"].includes(s.phase)} data-testid="skip">
          Skip story
        </button>
        {s.phase === "paused" && s.pendingResume !== null ? (
          <button className="primary" onClick={() => conv.resumeBrief(s.pendingResume!, true)}>
            Back to the brief
          </button>
        ) : s.phase === "paused" && s.interruptedAt ? (
          <button className="primary" onClick={() => conv.resumeBrief(s.interruptedAt!.segment_id, true)}>
            Back to the brief
          </button>
        ) : null}
        <button onClick={() => conv.next()} disabled={!["playing", "paused", "answering"].includes(s.phase)} data-testid="next">
          Next
        </button>
      </div>

      <section
        className={`talk phase-${s.phase}${s.speechDetected && s.phase === "listening" ? " hearing" : ""}`}
        data-testid="talk"
        role="button"
        tabIndex={0}
        aria-label={TALK_LABEL[s.phase]}
        onPointerDown={(e) => {
          if (e.button !== 0 || !brief?.manifest) return;
          onTalk("down");
        }}
        onClick={() => {
          if (brief?.manifest) onTalk("click");
        }}
        onKeyDown={(e) => {
          if ((e.key === " " || e.key === "Enter") && brief?.manifest) {
            e.preventDefault();
            if (s.phase === "ready") onTalk("click");
            else conv.interrupt("tap");
          }
        }}
      >
        {s.phase === "ready" ? (
          <div className="play-big" data-testid="play">
            ▶
          </div>
        ) : (
          <div className={`orb ${s.phase}`} />
        )}
        <div className="talk-label" data-testid="phase" data-phase={s.phase}>
          {s.phase === "idle" ? (brief ? "" : "Loading…") : TALK_LABEL[s.phase]}
        </div>
        {s.phase === "listening" && (
          <div className="small muted">{s.speechDetected ? "I'll answer when you pause" : "Go ahead"}</div>
        )}
        {(s.phase === "paused" || s.phase === "open_floor" || s.phase === "playing") && (
          <button
            className="interrupt-btn"
            onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              if (s.phase === "playing") conv.interrupt("button");
              else void conv.play(brief!.brief_id);
            }}
            data-testid="interrupt-btn"
          >
            {s.phase === "playing" ? "❚❚ Interrupt" : s.phase === "open_floor" ? "↺ Replay" : "▶ Play"}
          </button>
        )}
      </section>

      <div className="type-row">
        {typing ? (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              conv.sendText(typed);
              setTyped("");
              setTyping(false);
            }}
          >
            <input autoFocus value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="Type a question" />
            <button type="submit">Send</button>
          </form>
        ) : (
          <button className="link" onClick={() => setTyping(true)} disabled={!s.sessionId}>
            Type instead
          </button>
        )}
        <details className="debug">
          <summary>debug</summary>
          <div className="small mono">
            server={s.serverState ?? "-"} conn={s.conn ?? "-"} vad={s.vadEngine ?? "-"} seg={s.segmentIndex}
            <br />
            interrupt→pause={s.lastInterruptMs ?? "-"}ms · end-of-speech→audio={s.lastLatencyMs ?? "-"}ms
          </div>
        </details>
      </div>

      {s.phase === "ended" && s.sessionId && (
        <RatingSheet
          sessionId={s.sessionId}
          stories={script?.stories ?? []}
          hadFollowups={s.transcript.some((l) => l.role === "user")}
          onClose={() => setConv(resetConversation())}
        />
      )}
    </div>
  );
}
