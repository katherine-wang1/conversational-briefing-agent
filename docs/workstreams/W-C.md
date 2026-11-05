# W-C: Newsroom pipeline (N2–N6)

**Branch:** `ws/newsroom` (from `claude/mvp-proposal-design-td19a9`)
**Owns:** `app/agents/newsroom/`, `app/agents/prompts/newsroom.*.md`, `tests/unit/newsroom/`, `tests/fixtures/newsroom/`
**Contract changes:** additive only. List them in your summary.

## Read first
- `docs/parallel-work.md`
- `docs/02-technical-design.md` §7 (newsroom), §6 (beats), Appendix A/B
- `docs/01-PRD.md` §6.1 (NEWS-1..6), §6.4 (headline signals)
- `app/contracts/sources.py`, `digest.py`, `llm.py`, `tools.py`; `config/beats/*.yaml`, `config/taxonomy.yaml`, `config/agent.yaml`

Source parsers (N1) belong to W-A. Your pipeline takes `list[HeadlineObservation]` as input.

## Build
1. **N2 normalise**
   - Canonical URLs: strip tracking parameters, and resolve Techmeme and HN links to their target URL.
   - Dedupe.
   - Keep the best title and snippet.
2. **N3 cluster into stories** (`app/agents/newsroom/clustering.py`, a **reusable module**: the evaluation workstream will use it to compute hindsight labels)
   - Local embeddings (fastembed, CPU), agglomerative clustering with cosine threshold `tau`.
   - Pairs in the ambiguous band go in batches to role `newsroom.cluster_tiebreak` via `ModelClient`.
   - **Story identity across runs and days:** match new clusters to stories from the last `identity_lookback_days`, and set `update_of` when the same event has new facts.
   - Story ids must be stable and deterministic.
3. **N4 features**
   - Compute `StoryFeatures` exactly as in the contract, with tiers taken from the beat config.
   - Engagement is **as observed**, never a later value.
   - Also compute a rules-only `importance_rules` score, with weights in config (additive keys in `config/agent.yaml`).
4. **N5 score** (role `newsroom.score`, batches of about 20) → `DigestEntryLLM`. The prompt rules:
   - importance must cite features or sources as evidence, never background knowledge;
   - headlines must be specific and name the actors (UX-2);
   - snippets are wrapped as untrusted content;
   - up to `budgets.newsroom_run.fetches` verification fetches, through a `fetch` tool that's passed in (use the interface; don't implement fetching).

   Respect `hn_signal=false` (ablation A2): drop HN features and sources.
5. **N6 digest:** keep the top `digest_size` entries above a score floor. Produce a `Digest` plus a trace (steps, prompt hashes, token usage).
6. **Entry point:** `async def run_newsroom(beat_id, at, observations, model_client, tools, flags) -> tuple[Digest, Trace]`, with **no user context at all**.
7. **Prompts:** in `app/agents/prompts/`, as markdown with front-matter (role, version).

## Tests
- Realistic **test fixtures**: about 150 synthetic `HeadlineObservation`s across sources over 2 days, clearly marked as fixtures. Cover:
  - duplicates;
  - one story across several sources;
  - a developing story across days;
  - HN engagement.
- Use `app/llm/fake.py` if it exists, otherwise a minimal local fake.
- Test clustering determinism, identity across days, features, the A2 flag, and budget enforcement.

## Done when
- The pipeline runs end to end on fixtures with the fake model.
- The clustering module is documented for reuse.
- `pytest` and `ruff` are green.
