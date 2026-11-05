# W-D: Chief of staff + selection + memory + follow-up research

**Branch:** `ws/cos` (from `claude/mvp-proposal-design-td19a9`)
**Owns:** `app/agents/cos/`, `app/agents/memory/`, `app/agents/followup/`, their prompts in `app/agents/prompts/`, `tests/unit/cos/`, `tests/unit/memory/`
**Contract changes:** additive only. List them in your summary.

## Read first
- `docs/parallel-work.md`
- `docs/02-technical-design.md` §8 (chief of staff), §9 (memory), §14 (follow-ups), §19.1 (experiment flags), Appendix A/B
- `docs/01-PRD.md` §6.2, §8
- `app/contracts/brief.py`, `memory.py`, `digest.py`, `tools.py`, `llm.py`; `config/agent.yaml`, `config/experiments/*.yaml`

## Build principle
This **is** the agent we're building. Write the loops yourself on the `ModelClient` and `Tool` interfaces. No agent frameworks.

## Build
1. **C1 plan** (`cos.plan`) → `SearchPlan`: personal queries plus **adjacent-discovery** queries. Adjacent means related topics, companies, people and methods the user wouldn't know to search for, not random news.
2. **C2 bounded search loop** (`cos.search_loop` + `search` tool): refinement queries within the budget (≤ 12 searches). Results are wrapped as untrusted content.
3. **C3 candidate pool** (code): digest entries, personal and adjacent finds, carry-over items, and updates. Dedupe. Suppress repeats unless they're updates with new facts.
4. **C4 score** (`cos.score`, batched, structured) → `CandidateScores`.
5. **C5 selection: pure deterministic code**, **heavily unit-tested**. It handles:
   - slots (`big_news` / `for_you` / `outside_usual`);
   - MMR;
   - the importance floor for outside-usual stories;
   - `min_beat_share` and `carryover_max`;
   - listening order;
   - experiment overrides (B1 = digest importance only; B3 = no outside-usual slot).
6. **C6 source packs** (map-reduce): one `cos.extract_passages` call per document returns verbatim passages with character offsets, plus facts. Depth follows `dive_likelihood`; token caps come from config.
7. **C7 write brief** (`cos.write_brief`) → `BriefScript`:
   - a citation on every sentence;
   - specific headlines;
   - follows delivery preferences;
   - answered follow-ups first;
   - spoken style.
8. **C8 claim check** (`cos.claim_check`): judge each sentence against its cited passages. Allow one rewrite using only those passages, otherwise drop the sentence.
9. **Entry point:** `run_cos_daily(user_ctx, digests, memory, history, open_questions, flags, model_client, tools) -> (BriefScript, list[SourcePack], trace)`. Also implement **B5 naive mode** (`pipeline: naive`, TDD §19.1).
10. **Memory**
    - `memory.reflect` → `MemoryProposal`, from the transcript, signals and session context.
    - A **rules engine** implementing TDD §9.3 exactly:
      - explicit vs. implicit strengths;
      - the opportunity factor and the skip rule;
      - exposure-normalised Beta estimate and the blend;
      - decay with two half-lives;
      - pins, the concentration guard, and the floor.
    - The B2 static mode and the A3 `session_ctx_weighting=false` mode.
    - Onboarding extraction from free text (`memory.onboarding_extract`).
    - **The rules engine must be pure, deterministic, and exhaustively unit-tested.**
11. **Follow-up research:** a bounded loop (`followup.research`) → answered or unanswerable, with citations, then the claim check.

## Tests
- Use the fake `ModelClient` (`app/llm/fake.py` if present, else a minimal local fake) and fake tools.
- Fixtures: a digest, a memory state, and a history.
- Cover selection edge cases, every memory rule, budget degradation, and every experiment flag.

## Done when
- `run_cos_daily`, `apply_memory_update`, `extract_onboarding`, and `run_followup` work end to end with fakes.
- Prompts are written with front-matter.
- `pytest` and `ruff` are green.
