# Brief written for listening: before and after (2026-11-27)

Part 3 of `docs/architecture-revisit.md`, plus two defects from `../editor_to_brief/`: a story that
starts with no introduction (the claim check dropped its first sentence) and two consecutive sentences
that repeat one fact.

Inputs: the committed digests `../editor_to_brief/digest_{ai,energy,physical_ai}.json`, the synthetic
test user (`tests/unit/cos/fixtures.py`: interests agent memory, open models, Acme, SMR licensing),
`run_cos_daily` in process, OpenRouter with Haiku 5.5 for the per-user roles, Tavily for search. The
Anthropic key was unset. Script: `scripts/smoke_brief_audio.py`.

Two kinds of run:

- **full** (`before.txt`, `after.txt`): C1–C8 live. Search and fetch are live, so the stories differ
  between runs; only Nvidia appears in both.
- **packs** (`before_packs.txt`, `after_packs.txt`): C1–C6 replay the committed run (its C5 selection and
  `source_packs.json`), so only C7 (write) and C8 (claim check) call the model. Same stories, same
  passages: this is the controlled comparison.

"before" is the base commit 8929bfd; "after" is this change. Each line of the `.txt` files is
`[sid kind story_ref] text`.

## What changed

| | before (write v1, claim check v1) | after (write v2, claim check v2) |
|---|---|---|
| Intro | writer's greeting and count; spoke about follow-up answers when there were none | greeting, then a rundown built by code from cited teasers of the stories that survived C8 (count always right) |
| Story start | headline sentence, often with "a story picked for you" first | signpost line, then a spoken headline sentence |
| Sources | rarely named | named once per story ("TechCrunch, citing the Wall Street Journal, reports…") |
| Context, why it matters | absent | one context sentence from the passages; one line tied to a matched interest (only when the scorer matched one) |
| Story end | nothing | a hook inviting a question |
| Labels | "a story picked for you", "further from your usual reading" | slot names removed from the writer's payload; why-tags turned into speech |
| Story with a dropped first sentence | started mid-thought | the story's checked teaser becomes its lead; with no checked teaser, the story is removed |
| Repeated fact | kept | prompt rule (C7) and C8 rewrite rule; code drops adjacent near-duplicates (rapidfuzz `token_set_ratio` ≥ 85) |

## Side by side (packs: same stories and passages)

Opening:

> **before**: Good morning. There are four stories today. Answers to your follow-up questions come first.
> Our biggest story is about Nvidia and its chips in Southeast Asia.
>
> **after**: Good morning. You have three stories today: Nvidia pausing chip server shipments to Southeast
> Asia, a thousand Claude agents formalizing Fermat's Last Theorem, and the first US utility authorized
> to build a small modular reactor. First, the biggest story this morning.

The Fermat story:

> **before**: Next, a story for you on a team of AI agents doing advanced math. Anthropic researcher Lance
> Martin says 1,000 Claude agents self-coordinated over 30 hours … without a domain-specific harness.
> The run had no domain-specific harness and no human steering on the solution. *(same fact twice)* …
>
> **after**: Next, something for your work on agent memory. Lance Martin, a researcher at Anthropic,
> describes 1,000 Claude agents formalizing Fermat's Last Theorem in Lean. The run took about 30 hours
> and used no domain-specific harness and no human steering on the solution. … Since you follow agent
> memory, the coordination tables are the part to hear. I can tell you what those tables were for, if
> you'd like.

The video story (Agentic LongLive):

> **before**: Last, a story for you on AI that generates long videos. Its memory agent reads only the
> prompts so far, never the generated video. *(C8 dropped the headline sentence: "Its" refers to nothing)*
>
> **after**: removed. C8 rejected both its headline sentence and its teaser, so no grounded introduction
> was left (see "Remaining issues": the pack's fact cites a passage that doesn't state it).

In an earlier after-packs run during development (not kept), the same rule rescued the Fermat story: its
headline sentence failed C8, and the brief read "Next, something for your work on agent memory. It's about
a thousand Claude agents formalizing Fermat's Last Theorem." with the teaser's citations.

Full runs, the Nvidia story both times:

> **before**: Our first big story is about Nvidia pausing chip shipments in Southeast Asia. Nvidia has
> told manufacturing partners, including Dell and Super Micro, to pause server shipments to … *(then the
> next story)* Now, a few stories picked for you, starting with a new approach to AI agent memory.
>
> **after**: First, the biggest story this morning. Nvidia has told its server partners to pause shipments
> to Malaysia, Singapore, Thailand and Indonesia. TechCrunch, citing the Wall Street Journal, reports that
> Dell and Super Micro were among the manufacturing partners told to hold those shipments. … I can walk
> you through how that remote access worked, if you want the details.

Before, the full run's memory-lesson story was one sentence with no introduction ("Both the old and new
seat preferences looked current, and nothing said which one should win."): the claim check had dropped
its headline sentence. That's defect A again, in the before run.

| run | stories | sentences | words | est. seconds |
|---|---|---|---|---|
| before (full) | 5 | 19 | 327 | 131 |
| after (full) | 4 | 25 | 370 | 148 |
| before (packs) | 4 | 22 | 319 | 128 |
| after (packs) | 3 | 25 | 342 | 137 |

## Development run kept for the record: `v1_after.txt`

The first full after-run exposed a bug, fixed before the final `after` run. The writer left
`story_ref` off its signposts; when two stories were then removed (no passages; uncited sentences),
their signposts stayed as three empty lines in a row ("Now to chips and China's AI labs. Next, a nuclear
story… And finally, one from outside your usual beat."). Fix: an unattributed transition now goes with
the story it leads into, or the one it closes (`write._transition_refs`), and is removed with it. The
same run also had "If you're tracking export controls" for a story that matched no interest. Fix: the
payload now says "none (don't tie it to the listener's interests)" and the prompt forbids it.

## Remaining issues

1. **Dangling references after a C8 drop.** In `after.txt`, C8 dropped the loophole sentence, so "The
   U.S. Commerce Department closed that loophole" refers to nothing. Leads are now protected; middle
   sentences aren't. A next step would be to check a kept sentence that starts with "that" or "this" or
   a pronoun against a dropped predecessor.
2. **C6 facts cite passages that don't state them.** In LongLive's pack, the fact "Agentic LongLive is an
   asynchronous agentic memory system…" cites a passage that says no such thing (the claim comes from the
   title). The writer built the headline sentence and the teaser on it, C8 rightly rejected both, and the
   story was removed. That is grounding working, but the fix belongs in C6.
3. **Haiku doesn't always follow the prompt.** It wrote symbols in teasers ("ω", "9/4", "≠"), put commas
   in teasers (the rundown list gets hard to follow), and once put a definition before the headline.
   Code catches the cases that matter for grounding (unattributed transitions, numbers in uncited lines),
   not the style ones.
4. **C8 rewrites can restate the lead.** In `after.txt`, a rewrite brought "pause shipments" back in from
   the passage. Claim check v2 now says a rewrite must keep to the sentence's own point. I added that rule
   after the final full run; only the final packs run used it, and that run had no such repeat. The near-duplicate check covers adjacent sentences only,
   and this pair scores 68, below 85, because the rewrite also adds new facts.
5. **The rundown gets long** with four or more stories (about 45–60 words in one sentence). It could be
   split into two sentences when there are more than three teasers.
6. **Hooks aren't checked.** A hook offers detail ("how the export rules work") that the passages may not
   cover. The voice agent then has to say it lacks that detail.
7. **Token use:** C7 is about 8.2k tokens in and 2.7k out (before: 6.5k and 2.1k), and C8 about 9.5k in
   over four calls. That fits the 22.5k write reserve with room to spare, but more stories would get
   close.

## Cost

OpenRouter, from the ledgers: before full $0.0163, after full $0.0167, v1 after full $0.0147, before
packs $0.0027, after packs $0.0031, plus two earlier after-packs development runs (ledgers overwritten)
at $0.0033 and $0.0036. **Total: about $0.060.** Tavily searches aren't in the ledger.
