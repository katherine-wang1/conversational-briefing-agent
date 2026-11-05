# W-G: Simulated users + graders + labelling + test content

**Branch:** `ws/eval` (from `claude/mvp-proposal-design-td19a9`)
**Owns:** `eval/sim/`, `eval/graders/`, `eval/tasks/`, `eval/labeling/`, `eval/runner/`, `eval/analysis/`, `tests/unit/eval/`
**Contract changes:** additive only. List them in your summary.

## Read first
- `docs/parallel-work.md`
- `docs/02-technical-design.md` §15.4–15.6, §16 (runner), §17 (simulated users), §18 (graders, judge validation, statistics, failure analysis), §19.1
- `docs/01-PRD.md` §9 (simulated users), §10 (evaluation plan)
- `app/contracts/*`, `config/taxonomy.yaml`

## Build
1. **Simulated-user panel** (`eval/sim/profiles/panel_v1/*.yaml`, schema in TDD §17.1). **Hand-author** them yourself; no API calls.
   - 12 test profiles, 6 dev profiles, and a `sim_kate` placeholder.
   - Use only AI-beat subtopic ids from `config/taxonomy.yaml`, with realistic entities and keywords.
   - Each profile has:
     - a persona;
     - hidden interests (weights sum to 1);
     - **latent interests** (adjacent things they'd engage with but never state);
     - delivery prefs;
     - a drift schedule;
     - behaviour parameters.
   - **Cover every dimension in PRD §9.** Add `eval/sim/coverage.py`, which checks the coverage matrix.
   - Add 4 scenario users for T4: sudden drift, always rushed, contradictory, and narrow specialist.
2. **Simulator** (`eval/sim/simulator.py`): a per-turn loop (TDD §17.2) over the text session protocol (`app/contracts/session.py`).
   - Time budgets govern interrupts, questions, and session end.
   - About 15% of questions are unanswerable.
   - Latent interests are used only to decide engagement, never stated.
   - Structured action output.
   - A separate `sim.rate` call for per-story ratings.
   - Calls go through `ModelClient` (role `sim.user`), with a fake for tests.
3. **Code graders** (`eval/graders/`): implement every code metric in TDD §18.2 precisely, as pure functions over run outputs, each unit-tested with small fixtures:
   - importance recall@K,d, relative to B1;
   - relevance (overlap with hidden weights);
   - topic coverage + entropy;
   - **latent discovery**;
   - interest recovery (Spearman, NDCG@5, drift lag);
   - over-narrowing;
   - packaging length adherence;
   - specificity;
   - pre-fetch hit rate;
   - abstention correctness;
   - follow-up resolution;
   - latency;
   - injection canary detection.
4. **LLM-judge framework** (`eval/graders/judges/`) with judges for:
   - faithfulness: atomic claims vs. **cited passages only**;
   - relevance: story × profile;
   - packaging: depth and technicality;
   - QA: answer vs. supporting passage;
   - injection.

   Versioned prompts; judges are blind to config; roles `judge.*`; the output is labels plus a short rationale.
5. **Labelling app** (`eval/labeling/`, Streamlit) for the sessions in TDD §18.3:
   - L1: topic labels + "is this big news?";
   - L2: story × profile relevance;
   - L3: claims vs. passages, and QA answers.

   Saves to `eval/data/labels/*.jsonl` and computes accuracy and Cohen's κ against the judges.
6. **Runner skeleton** (`eval/runner/`):
   - the run-manifest schema (§16.1);
   - sequential days per user, with users in parallel;
   - a simulated clock;
   - one cache namespace per seed;
   - computation-reuse flags (§16.2);
   - trace writing.

   Code against the agent entry points' interfaces, with fakes.
7. **Statistics** (`eval/analysis/stats.py`): cluster bootstrap over users, paired comparisons, and a per-dimension breakdown.
8. **Hindsight labels** (`eval/archive/labels.py` is shared with W-A; coordinate through the orchestrator): implement the coverage label and the HN label from TDD §15.4, using W-C's clustering module through its interface. It runs once the archive and clustering are merged.
9. **Test content**
   - **50 prompt-injection documents** for T7: realistic news-style articles with planted instructions in varied styles (direct, hidden-text style, fake system messages, tool-call bait). Each has a unique canary string and the expected safe behaviour.
   - A **T3 question-generation script**, which builds the curated deep-dive QA set from source packs once the archive exists. Don't fabricate the set itself.

## Done when
- The panel and its coverage check, the simulator (with fakes), all code graders, the judge framework, the labelling app, the runner skeleton, the statistics, and the injection set exist.
- `pytest` and `ruff` are green.
