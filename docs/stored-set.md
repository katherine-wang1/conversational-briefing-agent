# Per-user stored set and voice access

Context: docs/architecture-revisit.md part 2; contract in `app/contracts/notes.py` (`UserStorySet`,
`StoredStory`, `StoryNote`), used unchanged.

## What gets stored

`run_cos_daily` (`app/agents/cos/pipeline.py`) builds a `UserStorySet` after C8, from C4/C5:

1. the brief's stories, in listening order (`in_brief=True`);
2. stories C5 picked but the claim check (C8) cut from the brief;
3. the next best stories by the C5 ranking, up to `stored_set_size` (default 20).

"The C5 ranking" beyond the brief is what C5 itself does when it grows past its slots:
`allocate_slots` puts every extra story in `for_you`, and unfilled slots are filled by for-you score
with MMR against what is already chosen. `selection.rank_beyond` continues exactly that from the
brief's picks (the importance-only arm B1 continues by big-news score instead; the naive arm B5 has
no pool, so its set is the brief). The carry-over cap doesn't apply: these stories aren't spoken.
Building the set makes no model calls.

`note_status`: `"shared"` for digest stories (and carry-overs/updates, whose refs are digest ids):
their note is written once at newsroom time by the notes work stream. `"per_user"` for the user's own
finds (`u:...` refs), which have no shared note.

Config (`config/agent.yaml`): `stored_set: {size: 20, extra_packs: 0}`.

## How it is returned and persisted

- Returned on the trace: `trace.story_set` (a `RunTrace` field excluded from the trace JSON; a
  `stored_set` trace event lists the refs). `run_cos_daily` keeps returning
  `(script, packs, trace)`, so the job ports, eval harness, scripts and tests are unchanged.
- Persisted as a JSON column on the brief: `briefs.story_set jsonb`
  (`supabase/migrations/20261127000300_briefs_story_set.sql`, `BriefsRepo.json_cols`). The
  `cos_daily` handler writes it with the script in the same upsert, re-stamped with the handler's
  `brief_id`/`user_id`. Why a column rather than a table: the set is written once per brief, always
  read whole (by the voice session, together with the brief row it already loads), has the brief's
  lifetime and RLS, and needs no per-story queries. A table is easy to add later if per-story
  queries (e.g. "was this story stored for anyone") are needed.
- Read by the WebSocket session (`app/agents/voice/ws.py`): `briefs.story_set` → `UserStorySet`, or
  None for briefs written before the column existed (the voice agent then behaves as before).

## Extra packs (decision: default 0)

Per-user finds have no shared note. `stored_set_extra_packs: N` builds a light pack (1 document,
`depth="light"`) for the top N non-brief finds, concurrently with the brief's packs, inside the
research budget; the packs come back with the others and land in the voice CORPUS.

Default 0 because:
- Cost: each light pack is 1+ fetches and one `cos.extract_passages` call (~$0.0003–0.0006 on Haiku),
  plus ~700 chars per passage in every voice turn's prefix (cached, but it grows the prefix for
  stories most users never ask about).
- Latency: the packs share C6's concurrency (`cos_packs: 4`), so extra packs can lengthen C6, which is
  on the brief's critical path; failed fetches retry further URLs.
- Coverage: in the live smoke 13 of 20 stored stories were finds; a small N covers few of them, and
  the find's search snippet (its `summary`) plus `quick_search` already give the voice agent a
  grounded fallback.

Turn it on (e.g. 2–3) if voice evals show listeners asking about non-brief finds often and the
fallback answers are too thin.

## Voice access

- L1 (`app/agents/voice/context.py`, `render_stored`): a "STORED STORIES" block in the cached prompt
  prefix, between BRIEF and CORPUS: `story_id=<ref> [in brief] <headline> — <one line>`.
- L2: tool `get_story(story_ref)` (`app/agents/voice/tools.py`, `VoiceSession._get_story`):
  - a `shared` story with a note in the `NoteStore` → the note (cited facts with `[p:note:<ref>:<n>]`
    ids, entities, context, why it matters, open questions), wrapped as untrusted source text; the
    fact ids resolve to the note's source URLs for on-screen citations;
  - no note (or a `per_user` story, or no store configured, or a store error) → the stored
    headline, summary (cite id `[p:stored:<ref>]`, resolving to its first link) and links, marked
    "no detailed note is stored" so the model falls back to `quick_search` or a bookmark; if the run
    built a pack for the story (e.g. a C5 pick cut by C8), it is pointed at the CORPUS instead;
  - unknown refs are refused (voice tools only ever take refs from the user's brief or stored set).
- `NoteStore` (`app/agents/newsroom/note_store.py`): `async get(story_id) -> StoryNote | None`.
  `InMemoryNoteStore` for tests/replay/smoke; `DbNoteStore` reads the latest row by `created_at` from
  the shared `story_notes` table (owned and migrated by the notes work stream) and returns None
  when the table doesn't exist yet (remembered, so it isn't asked again) or on any read error.
  The WebSocket uses `app.state.note_store` if set, else `DbNoteStore(db, user)`.
- Prompt: one separate section, "Stored stories (beyond the brief)", at the end of
  `app/agents/voice/prompts/voice.turn.md`.

## Open decisions

- Mix of the stored set: the for-you continuation favours high-relevance finds (13 of 20 in the live
  smoke), which have no shared note, and drops important digest stories with low relevance. Options:
  reserve a share for digest stories (e.g. by big-news score per followed beat), or rank by
  `max(for_you, big_news)`.
- Memory signals: `get_story` on a non-brief story records a `dive` on it, but `memory_update` only
  learns from stories in `session.shown` (the brief), so those dives are currently ignored. Same for
  `save_for_later` on a stored story: it is saved, but the carry-over path reads saved items from the
  shown history.
- `NoteStore.get` is async (the DB store needs it); the notes work stream should match this.
