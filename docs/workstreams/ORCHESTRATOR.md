# Orchestrator brief: build all workstreams in parallel

You are the **orchestrator** for the build of the conversational briefing agent (CS329Z project, deadline **Wednesday, December 2, 2026**). You coordinate seven workstreams, delegate each to a sub-agent, review and merge their work, and then integrate the pieces into a working system.

## 0. Read first (in this order)

1. `docs/parallel-work.md`: workstreams, ownership, rules, merge order.
2. `docs/02-technical-design.md`: the design. §25 has the dated plan; §26 the open questions with defaults.
3. `docs/01-PRD.md` §6.0 (build principle), §10 (evaluation plan), §11 (research guardrails).
4. `docs/02a-technical-design-options.md` §1.4 (lean budget rules) and §12–13 (decisions and design defaults).
5. `app/contracts/` and `config/`: the shared contracts every workstream builds against.
6. `docs/workstreams/W-A.md` … `W-G.md`: one brief per workstream.

## 1. Ground rules

- **Branches:** the base is `claude/mvp-proposal-design-td19a9`. Kate has given permission to push to:
  - `claude/mvp-proposal-design-td19a9` (integration);
  - `ws/*` (one branch per workstream);
  - `data/archive-raw` (collected archive shards).
  
  Don't open pull requests unless Kate asks.
- **Build principle:** we build the agent loop, tools, memory, selection, and evaluation. Models are called only through `app/contracts/llm.ModelClient`. No agent frameworks, hosted agent platforms, or provider-side tools (such as a model provider's built-in web search).
- **No fabricated data or results.** If a host or key is unavailable, say so and build against fixtures.
- **Spend caution** (credits are a ceiling, not a target): $200 OpenRouter + $100 Tinker for the whole project. **Total live model spend in this session ≤ $10** (spikes + smoke tests). No evaluation runs in this session. Log every live call through the ledger once W-B exists.
- **Secrets:** read keys from environment variables only (`.env.example` lists the names). Never print or commit them.

## 2. Preflight (do this yourself, before delegating)

1. **Network:** check with curl that these hosts are reachable:
   - `web.archive.org`, `www.techmeme.com`, `hn.algolia.com`, `news.ycombinator.com`, `export.arxiv.org`, `huggingface.co`, `openrouter.ai`;
   - Tinker's API host;
   - 3–4 domains from `config/beats/*.yaml`.
2. **Keys:** check which of `OPENROUTER_API_KEY`, `TINKER_API_KEY`, `SEARCH_PROVIDER` / `SEARCH_API_KEY` are set (presence only; never print values).
3. **Tooling:** run `python3 -m venv .venv && .venv/bin/pip install -e ".[dev]" && .venv/bin/python -m pytest -q` and confirm green. Check that Docker works (for W-E's local Postgres).
4. Report a one-screen preflight summary to Kate: what's reachable, what's missing, and which workstreams are affected.

**Known gap:** the live web-search key may be missing. If so, W-E builds the `search` tool with Tavily, Exa, and Brave adapters selected by `SEARCH_PROVIDER`, tests with mocks, and the replay backend (archive FTS) works without any key. Live search waits for Kate to add a key.

## 3. Delegate: launch sub-agents

Launch one sub-agent per workstream, **each in its own git worktree** (Agent tool with `isolation: "worktree"`), giving it the full contents of its `docs/workstreams/W-*.md` brief plus this instruction: "Work on branch `ws/<name>` based on `claude/mvp-proposal-design-td19a9`, push it, and finish with a summary of what you built, test status, any additive contract changes, and anything blocked."

**Launch order:**
- **Wave 1 (now, in parallel):**
  - **W-A archive** (most time-critical: the evaluation waits on it);
  - **W-E platform**;
  - **W-B model layer**;
  - **W-D chief of staff**.
- **Wave 2 (as soon as a wave-1 slot frees up, or right away if concurrency allows):**
  - **W-C newsroom**;
  - **W-G evaluation**;
  - **W-F web app**.

**Long-running archive collection:**
- A sub-agent can't babysit a multi-hour crawl. W-A builds and tests the collectors and runs a short smoke collection (1–2 days of data), then hands back the exact command.
- **You** then start the full collection as a background process (Bash `run_in_background`), in W-A's worktree.
- Have the collector push shards to `data/archive-raw` periodically, so data survives if the container is reclaimed.
- Monitor progress and restart from the resumable cache if it stops.

## 4. Review and merge

As each sub-agent finishes:

1. Check out its branch. Run `ruff` and `pytest`, including `tests/contract/`.
2. Review any contract changes. They must be **additive** (new optional fields, new files). Reject renames and removals.
3. Merge into `claude/mvp-proposal-design-td19a9` in the merge order from `docs/parallel-work.md`:
   1. W-E platform
   2. W-B llm
   3. W-A archive
   4. W-C newsroom
   5. W-D chief of staff
   6. W-G eval
   7. W-F web
   
   Rebase later branches on the merged result. Resolve conflicts yourself.
4. Push after each merge.

## 5. Integrate (after merges)

Replace fakes with real components and get one **live end-to-end run** working locally:

1. Newsroom: the real source parsers (W-A) + the real model layer (W-B) → one **live AI-beat digest**.
2. Chief of staff (W-D) with real tools (W-E) + the model layer → one **brief + source packs** for a test user created through onboarding.
3. Voice session server in text mode: a scripted session (interrupt → question → answer → resume → end) → memory update applied.
4. The job queue runs `newsroom_run` and `cos_daily` for the test user.
5. The web app (W-F) against the real backend locally: the brief plays with fake or real TTS; the interrupt flow works in desktop Chrome.

Use the cheapest models available for these smoke tests (`config/models.yaml` `dev_default`). Pick one cheap OpenRouter model and set it there, and stay within the $10 cap.

## 6. Final report to Kate

Write `docs/status.md` and give Kate a short summary covering:
- what works end to end, and test status;
- archive collection progress and the feasibility findings (especially HN front-page coverage per day);
- spike results (models: tool calling, JSON mode, caching, cost, time to first token, training cutoffs; Tinker);
- a **recommended replay window** based on the model cutoffs and archive coverage;
- everything Kate must do herself:
  - create the Supabase and Railway projects (steps in `docs/deploy.md`);
  - add the live search key;
  - run `docs/iphone-checklist.md` on her phone;
  - make the open decisions from TDD §26;
- next steps, mapped to the TDD §25 plan (W2: Kate's self-study starts Sunday, November 15).
