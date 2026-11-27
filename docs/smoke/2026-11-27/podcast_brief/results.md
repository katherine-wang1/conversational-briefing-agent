# Podcast brief and the live app path: 2026-11-27

**Result:** the real app (`./scripts/dev.sh`: Postgres, API + voice server, worker, built web app) runs
end to end on this branch, with the new pieces: shared newsroom → story notes → per-user brief with a
stored set → audio → voice session that can talk about stored stories. The brief is now written as a
podcast (prompt v3), with bridges instead of segment labels.

## Setup

- Fresh database, `./scripts/dev.sh` (unchanged quick start), dev-bypass user, headless Chromium driven
  by the 2026-11-06 drivers (`docs/smoke/2026-11-06/web-e2e/driver/`), onboarding "~4 min".
- Interests typed at onboarding: "AI agents and agent memory research, open-weight models, and small
  modular reactor licensing (keep me posted)".
- Newsroom: Sonnet 5.5, prompt v4 (unchanged). Story notes: Haiku. Per-user run: Haiku, except the brief
  writer (Sonnet 5.5, reasoning low; see below). Search: Tavily.
- Brief iterations used `scripts/rebrief.py` (new): it rebuilds today's brief through the real
  `cos_daily` handler on the stored news, so each try costs one per-user run, not a newsroom run.

## Results

| Check | Result |
|---|---|
| Fresh DB → onboarding → first brief ready (newsroom for 3 beats + notes + brief) | 226 s |
| Re-onboarding on stored news → brief ready | 187 s |
| Browser: play, interrupt, ask (stubbed speech), spoken answer, resume, play to end, end | 14 of 14 checks pass (`browser_checks.txt`) |
| End of question → first answer audio | 3.2 s |
| Voice answer from a brief story ("how much of China's computing power…") | Correct, grounded (24 percent, Institute for Progress), and it noticed the figure belongs to the other Nvidia story |
| "What else happened today?" | Answered from the stored set (Anthropic shares, SAP cuts, the Trump AI task force) |
| A story not in the stored set (Meta Muse) | Correctly said it had nothing on it today and offered to look into it |

**Before** (prompt v2, Haiku writer): `brief_before_v2_haiku.txt`. Anchor signposts ("Now to funding in
AI agents.", "And finally, one from outside your usual beat."), a five-story rundown in one breath, one
sentence of context per story.

**After** (prompt v3, Sonnet writer): `brief_after_v3_sonnet.txt`. Bridges that follow the content
("Chips and who gets to build with them also sit behind Tesla's latest deal.", "All that AI needs
electricity, and a Japanese power giant has a plan for that."), background on every story (what the
company is, why it matters), related stories next to each other, a rundown that names three stories,
no category names. 5–6 stories, 180–240 s of speech.

## What changed (code)

- `cos.write_brief` v3: podcast host, bridges (no section/beat/slot names, no stock fillers, links only
  where both stories' passages show them), what happened → why → background → why it matters, length
  scaled by what this listener has heard, may group connected stories.
- Shared story notes go to the writer as citable passages (`note_fact`, `note_context`;
  `app/agents/cos/brief_notes.py`). The writer is told, per story, whether it is new to this listener or
  a follow-up to one they heard (from `is_update_of` and their history).
- Claim check: a story sentence is also judged against its story's note passages; a sentence that
  restates one passage (numbers, negations and hedges kept) is accepted by code. Before this, Haiku's
  check dropped near-verbatim headline sentences and the stories lost their openings.
- Near-duplicate filter compares with every earlier sentence of the story.
- 6 stories at ~4 min: 40 s per story (`selection.seconds_per_story`); onboarding lengths now match
  their labels (~2/~4/~7 min = 120/240/420 s).
- Stored set: at least half shared stories beyond the brief (`stored_set.min_shared_share`); search
  finds dropped before scoring when they are social posts, quote/listing/event pages, pages without a
  real headline, or near-duplicates.
- Voice prompt v5: same host voice; never narrate tool use; attribute social posts.

## Model choice for the writer

Haiku followed the v3 bridge rules unevenly in three runs (stock fillers like "Something quite different
now", missing bridges, a bridge dropped for containing a number). Sonnet 5.5 (reasoning low; OpenRouter
requires reasoning for it) followed them. Cost: about $0.06 per brief for the writer, against $0.003 on
Haiku; a whole per-user run is now about $0.08–0.09 plus about $0.01 of audio. This is a decision for Kate
(plan D21).

## Still wrong

1. Bridges aren't fact-checked. One run said "Nvidia also shows up in a very different story" before the
   Tesla story (no Nvidia in it). The prompt now forbids links the passages don't show; not yet verified
   over many runs.
2. Run-to-run variance in which stories are picked (search results differ), so two rebuilds of the same
   day can differ a lot.
3. Voice still sometimes says "the sources don't detail…" despite prompt v5.
4. Some per-user finds are still weak (a blog post on open-weight models next to the Fortune story).
5. The app still shows the slot label ("big news") on screen; that is for the UI redesign.

## Cost of this work

$1.18 OpenRouter in total (`cost_by_role.txt`): newsroom $0.32 (one run, 3 beats), story notes $0.06,
twelve per-user brief builds about $0.61 (the writer $0.41 of that, most of it the Sonnet trials),
audio $0.19, voice $0.006. Plus a few hundred Tavily searches.
