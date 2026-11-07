# Midway report: outline and evidence (for Kate to write up)

**Not report text.** The CS329Z logistics page says "AI is not allowed to be used to write the course project reports"; AI is allowed "for brainstorming and feedback on your writing." This file is a brainstorm: the points to make in each section, with the numbers and where they come from. Write the prose yourself, then I can give feedback on your draft.

## Assignment requirements (cs329z.stanford.edu/project.html)

- 3–4 pages, ICLR submission template (Overleaf link on project.html). References don't count toward the page limit. No prior-work section needed.
- Sections, in order: Research Questions / Problem Specification → Environment & Data → Methods → Summary of Progress → References → AI-Use Disclosure Statement.
- Graded "mainly on the Environment & Data, Methods, and Summary of Progress sections." Worth 5% of the course grade. The demo video is a separate 5% and goes in with a GitHub link and a README for running the prototype.
- **Due Fri Nov 6, 11:59 pm (already passed).** Ed #56 mentions 3 free late days per course. Check that they apply to this milestone.

Suggested page budget: Problem 0.3 · Env & Data 1.1 · Methods 1.3 · Progress 0.8 · (+ refs, AI disclosure in the appendix).

---

## 1. Problem specification (one paragraph)

Points to make (reuse the proposal's framing, but shorten it):
- **User and need:** someone who must keep up with fast-moving AI news but has little time. Typing and scrolling don't fit a commute, so the brief is spoken.
- **What the agent does:** a short spoken morning brief with 5–7 cited stories. You can interrupt at any point to ask questions. It learns interests and delivery preferences from the conversation.
- **The three-way trade-off in each brief:** global importance, personal relevance, and exploration (avoid a filter bubble).
- **Research questions** (state them explicitly, since graders look for them):
  - RQ1: Does memory written from conversation improve relevance over a static profile, without losing importance recall?
  - RQ2: Does an explicit exploration slot keep topic coverage up and surface latent interests?
  - RQ3: Does pre-fetching source packs let the agent answer most follow-ups from cache at voice latency?
  - RQ4: Are the brief and the answers faithful to their cited sources (≥ 95% supported claims)?
- Optional: one line on what changed since the proposal. The design added a shared **newsroom** stage that is separate from the per-user agent, plus ablations of that separation (A1).

## 2. Environment & Data (heavily graded: be concrete)

**2a. Environment: tools and runtime modes**
- Two runtime modes, **same agent code**. Only the tool backends and the clock differ (TDD §2.2):
  - `live`: web search (Tavily, news topic), HTTP fetch, live headline sources, wall clock.
  - `replay`: a frozen archive (SQLite + FTS5 search, archived pages, dated headline observations) on a simulated clock. This is what makes evaluation reproducible.
- Agent tools: `search`, `fetch`, `headline_source`, `hn_comments`. Voice tools: `quick_search` (3 s timeout), `bookmark_question`, `save_for_later`, `note_preference`, `navigate`.
- All tool output is wrapped as untrusted content (prompt-injection defence, tested by T7).
- Beats: AI (primary). Energy and physical AI are also configured. The AI beat has 9 subtopics in `config/taxonomy.yaml` and ~17 headline sources across tiers: Techmeme, HN front page, HF papers, arXiv cs.CL/LG/AI, lab blogs, tech press (`config/beats/ai.yaml`).

**2b. Evaluation data: the frozen corpus** (`docs/status.md` §2, `docs/archive-datasheet.md`, `docs/spikes.md` § Archive)
- Collection window 2026-08-01 → 2026-10-31. Proposed replay window **2026-09-12 → 2026-10-21**: 10 dev days, then 30 held-out test days.
- How the window was chosen (TDD §15.6): it starts after the agent models' published training cutoffs (Claude 5.5: Jun 2026; GPT-6 Luna: May 18, 2026), ends ≥ 14 days before today so the hindsight labels are complete, and needs ≥ 90% daily Techmeme/HN coverage.
- Collected so far (cloud run): HN Algolia 91k items, HN front-page labels for 5,519 stories, arXiv ~48.7k papers, HF papers 2,355, RSS backfill, 712 Wayback availability probes.
- Feasibility numbers worth reporting:
  - Wayback same-day capture: HN 89/92 days (97%), Techmeme 88/92 (96%). Captures within 1 h of the 06:00 UTC brief time exist on only 52/92 days, which is a risk.
  - Article bodies from origin: 70% extracted. 22/80 had a page date that didn't match the publish date, so origin copies may be later revisions. That's why Wayback copies are preferred.
  - Wayback throttles at ~15–20 req/min, so the full run is 35–70 h (on the laptop; web.archive.org is blocked in the cloud).
- **No-leakage rule:** the agent sees only what was observable at brief time (as-of rank and points). Final points and later coverage are used only for labels. The provenance column in the datasheet records which is which.

**2c. Labels and task sets** (TDD §15.4, §18.1)
- Hindsight importance labels:
  - Primary: a story counts as important if it's covered by ≥ k independent domains within 14 days. k is calibrated to about 8 important stories a day.
  - Secondary: HN points above a cutoff. Labels only, never agent inputs.
  - Topic labels come from an LLM, and you spot-check 200 stories.
- Simulated-user panel: 18 profiles (12 test + 6 dev). Each has hidden interest weights, latent interests that are never stated, delivery preferences, scheduled drift (add/fade), a time budget (rushed vs. relaxed), explicitness and noise. One profile is calibrated to you. Panel coverage is checked in code.
- Task sets T1–T8, with sizes: T1 selection (12 users × 30 days = 360 briefs per config), T2 brief faithfulness (~1,000 judged claims + 150 human-checked), T3 deep-dive QA (~250 curated: answerable / needs-search / unanswerable), T4 memory trajectories, T5 follow-ups (≥ 100), T6 system cost/latency, T7 safety (50 planted-injection documents + the isolation suite), T8 real use (your 20–35 self-study sessions).

**2d. Success criteria and metrics** (TDD §18.2; a table works well here)
- Code graders: importance recall@K (within 2 days, relative to B1); relevance against hidden weights; topic entropy; latent discovery; interest recovery (Spearman ρ / NDCG@5, drift lag); over-narrowing ratio; pre-fetch hit rate; latency; injection success; isolation.
- LLM judges: citation faithfulness (atomic claims vs. the cited passages only), QA correctness, relevance (secondary), packaging. Judges run on Tinker Inkling, a different model family from the agent (Claude), and are validated against your labels with Cohen's κ ≥ 0.6.
- Statistics: cluster bootstrap over users with 95% CIs; paired comparisons on the same (user, day) and simulator seed.
- Targets from the proposal: beat the static profile (B2) on relevance while staying close to B1 on importance recall; topic coverage above B3; ≥ 95% supported claims; most follow-ups answered from the pre-fetch cache; ≤ 1 s to first audio.

## 3. Methods (heavily graded)

A figure is worth the space: the three-part architecture (TDD §2.1 / §2.3 diagram, redrawn).

- **Build principle:** you build the orchestration, tools, memory, selection and evaluation. LLMs, speech, search and infrastructure are off-the-shelf components behind thin interfaces. No hosted agent platforms or provider-side tools.
- **Newsroom (shared, no user data; Postgres row-level security enforces this):**
  - N1 collect → N2 normalise → N3 embedding clustering (τ = 0.78, LLM tie-break in the ambiguous band) with story identity across days → N4 features (independent domains, front-page hits, velocity, …) → N5 LLM importance score.
  - In N5 the model must cite features or sources as evidence, not its own background knowledge, and may make ≤ 5 verification fetches. A rules-only score is kept for comparison.
  - N6 writes a digest of 30 stories per beat.
- **Chief of staff (per user, daily, before brief time):**
  - C1 plan (≤ 6 personal + ≤ 4 adjacent-discovery queries) → C2 bounded search loop (≤ 12 searches) → C3 candidate pool (~40–80) → C4 LLM scoring (relevance with linked memory items, adjacency, novelty, dive likelihood).
  - **C5 selection in deterministic code:** slots big_news 2 / for_you 3 / outside_usual 1, with MMR (λ = 0.7). Why code rather than an LLM: it's auditable and the ablations can switch parts off.
  - C6 source packs: verbatim passages with character offsets, deeper when dive likelihood is high. This is the pre-fetch.
  - C7 write the brief, with every sentence citing passage ids → C8 claim check (rewrite or drop unsupported sentences) → TTS.
- **Live voice agent:**
  - Server state machine (READY → PLAYING → LISTENING → THINKING → SPEAKING → resume/OPEN_FLOOR → ENDED).
  - Per-turn context: brief + source packs (cached prefix), the session so far, and the playback position, used to resolve "that one".
  - Answers come from the corpus first. Otherwise it quick-searches or bookmarks. No claim check on live turns, for latency.
- **Memory:**
  - The LLM proposes updates (`memory.reflect`). **Code applies the rules**: explicit signals η = 0.30, implicit signals η = 0.15; skips count only when the user had time (opportunity factor, and skipped in ≥ 2 of the last 4 sessions); exposure-normalised Beta estimate; decay half-life 21 days (implicit) / 60 days (explicit); a 35% cap per subtopic against over-narrowing.
  - This split between proposal and rules is a design decision worth explaining. It is also what ablation A3 tests.
- **Follow-up research:** a bounded loop (≤ 8 searches, 6 fetches) for bookmarked questions. The answer, claim-checked, opens the next brief.
- **Model layer:** a role router over OpenRouter and Tinker, call cache, cost ledger, hard spend cap. The current default for every role is Claude Haiku 5.5, chosen in the spike (`docs/spikes.md`: cheapest tier, tools and strict JSON first try, −92% cost on cached prefixes, ~0.7 s TTFT). A bake-off will pin a model per role.
- **Baselines and ablations** (`config/experiments/`; a small table):
  - B1 importance-only, B2 static memory, B3 no exploration slot, B4 no pre-fetch, B5 naive single LLM call with search + profile text.
  - A1 no newsroom separation, A2 no HN signal, A3 no session-context weighting.
  - The self-study adds blinded B1/B2/B3 days (~20–25% of days).
- **Client:** iPhone PWA. Single audio element, interrupt by tap / on-screen button / AirPods (Media Session), VAD end-of-speech detection, browser or server STT.

## 4. Summary of progress (heavily graded: be honest and specific)

**Done (with evidence):**
- Full system built and integrated: platform, model layer, newsroom, chief of staff, memory, voice, web PWA, archive collectors, evaluation harness. 533 pytest tests pass, including 98 isolation tests, so users can't see each other's data via SQL, API or WebSocket.
- Live end-to-end in the browser with real audio (`docs/smoke/2026-11-06/web-e2e/`):
  - The flow sign-in → onboarding → brief → play → interrupt → ask → spoken cited answer → resume → end → memory update all passes.
  - First brief ready 111 s after onboarding: 6 stories, 2 big news / 3 for you / 1 outside usual. 10 integration bugs found and fixed.
- Memory works on a real session: after a question, the interest "open-weight model releases" went from weight 0.40 to 0.775 (`docs/smoke/2026-11-05/`).
- Story identity across runs: 97/98 stories matched between two newsroom runs.
- TTS (`docs/smoke/2026-11-06/tts/`): verbatim on 19/19 brief sentences, checked by two independent STT models. First audio has a median of 0.85 s. Cost $0.0044/min. A plain prompt dropped words; a strict prompt fixed it.
- Live search: 3 of 4 stories in a test brief came from search (`docs/smoke/2026-11-06/search/`).
- UI (Nov 7): voice speed, readable citation chips, a live debug log.
- Cost so far: well under a dollar of model spend for all the smoke runs (e.g. $0.18 for the full e2e run), against a $200 + $100 budget.

**Remaining** (the dates come from the TDD §25 plan):
- Finish the corpus: laptop Wayback run (35–70 h), HN comments and bodies, datasheet v0, hindsight labels. Confirm the window.
- Deploy (Supabase + Railway) and run the iPhone checklist. Self-study starts **Sun Nov 15**.
- Labelling sessions L1–L3 (~3.5 h). Replay backends in the runner. Model bake-off on the dev days. Judge validation. Code freeze, then test runs **Tue Nov 24**. Grading, failure analysis and the report in the week to Dec 2.
- **No evaluation results yet.** Say this plainly.

**Obstacles and risks** (be specific; the rubric asks for "concerns that could stop the project"):
- **Latency:** end of speech → first answer audio is 2.5–3.2 s against a ≤ 1 s target. Planned fix: stream the TTS audio (TODO 1.20).
- **Archive:** Wayback throttling and the long laptop run. Brief-time coverage is only 52/92 days within 1 h. The fallback is to drop HN as a signal (OQ-14).
- **Leakage:** some candidate models (DeepSeek V4.1, Qwen 3.8) may have training cutoffs inside the window. Keep them out of the scoring roles, or run a leakage probe.
- **Grounding gaps:** paywalled or bot-blocked fetches (wsj 401, openai.com 403) left big-news stories without passages, and they were dropped from the brief. "For you" stories can lack variety. Search results are sometimes Google redirect URLs.
- **Simulator validity:** prior work (Zhu et al., 2024) shows LLM user simulators can be unreliable. The mitigation is the sim_kate agreement check (κ) and real self-study.
- **One-person team with one real user:** T8 is small, so the simulated panel carries the statistics.
- Voice model quirks: it sometimes narrates its reasoning (now filtered and logged) and bookmarks too eagerly.

## 5. References

Same style as the proposal: alphabetical, full author names, year, title, venue. Most proposal references still apply. Methods could add:
- MMR (Carbonell & Goldstein 1998), already in the proposal.
- Gao et al. 2023 and Liu et al. 2023 (citation faithfulness), already in the proposal.
- Zhu et al. 2024 (simulator reliability), already in the proposal.
- Optional: an LLM-as-judge validation paper and τ-bench (Yao et al. 2024).

## 6. AI-Use Disclosure: facts to disclose (write the statement yourself)

- **Code:** almost all of the code was written by Claude Code agents in parallel workstreams (W-A … W-G, then integration), from your PRD and technical design. You made the design decisions and the open-question calls and directed the work. You need to be able to validate and explain all of it (oral exams).
- **Data:** Claude wrote the archive collectors and the simulated-user profiles. The 50-item injection set was authored with AI. The judges and simulators are LLMs (Tinker Inkling). Human labels will come from you.
- **Smoke tests and analysis:** run and written up by Claude agents (`docs/smoke/`).
- **This report:** Claude collected the facts and wrote this outline from the repo docs. You wrote the text. (If Claude also gives feedback on your draft, disclose that too.)
- Note: the proposal's disclosure says Claude produced a first draft of the proposal. The logistics page forbids AI-written reports. Don't repeat that for this report, or ask staff first.
