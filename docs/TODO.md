# TODO

Two tracks. **Track 1** is the working agent for the midpoint demo; **Track 2** is evaluation and can wait.
Owner: **Kate** = needs your accounts, keys, decisions or phone; **Claude** = code or docs work; **both** = Kate supplies something, Claude does the rest.
Background and evidence: `docs/status.md`.

## Track 1 — Working agent (midpoint demo)

### Must-have for the video
- [x] **1.1 Text-to-speech** (both). Pick a provider (OQ-7) and add `TTS_API_KEY`; Claude wires and tests it. Right now the backend makes placeholder audio, so the agent can't speak.
  Done 2026-11-06: `TTS_PROVIDER=openrouter_audio` (gpt-audio-mini via the existing OpenRouter key, WAV, ~$0.0044/min, verbatim with a strict prompt + transcript/length guards); results in `docs/smoke/2026-11-06/tts/results.md`. Still to do: a Safari/iPhone listening check.
- [x] **1.2 Search key** (Kate). Done 2026-11-06: live search works, and a test brief got 3 of 4 stories from search (`docs/smoke/2026-11-06/search/`). Still needed locally and on Railway. Set `SEARCH_PROVIDER=tavily` and `SEARCH_API_KEY`. Without it, briefs use only the daily digest and follow-up questions can't look anything up.
- [ ] **1.3 Full run in the browser against the real backend, with sound** (both). Play → interrupt → ask → hear the answer → resume → end. Fix what breaks, including the two known mismatches between the web app and the backend: `/api/history`, and asking for a brief before onboarding (409/202).
- [ ] **1.4 Use it yourself for a few days before recording** (Kate). Onboard, then listen to daily briefs, so the video can show memory and learning across days.
- [ ] **1.5 README for running the prototype** (Claude). Required for the submission: setup, env vars, local run with `docker compose` plus the web dev server, and a short tour of the architecture.

### Should-have
- [ ] **1.6 Save which interest each candidate story matched** (Claude). Then skipping a story teaches the agent something; today `ShownStory.interest_ids` is empty.
- [ ] **1.7 Run follow-up research live once** (Claude). It only has unit tests so far.
- [ ] **1.8 Apply memory decay before each run** (Claude).
- [ ] **1.9 Quick fallback brief when the full run takes too long** (Claude). See TDD §20 and the TODO in `app/api/routes.py`.
- [ ] **1.10 Tune the clustering threshold** (Claude). `tau` 0.78 hasn't been tuned on real embeddings; check the live digests for duplicate or merged stories.

- [ ] **1.17 Clean up search results** (Claude). About half of Tavily news hits are Google redirect links, so the source shows as "google.com". Use the address the fetch ends up at for the citation and source. Also parse dates as UTC.
- [ ] **1.18 Keep the "big news" slot when fetches fail** (Claude). Paywalled or bot-blocked fetches (wsj.com 401, openai.com 403) left both big stories with no grounded passages, and the brief dropped them. Fall back to other articles on the same story or to the digest snippet. Also improve variety in the "for you" stories.

- [ ] **1.19 Smaller audio files** (Claude). Text-to-speech returns raw audio, which we save as WAV (48 KB/s, about 23 MB per 8-minute brief). Convert to MP3 on the server (ffmpeg in the Docker image) for about 8× less.
- [ ] **1.20 Faster spoken answers** (Claude). Each answer sentence is sent only when its audio is complete (about 1.4 s), which misses the ≤ 1 s target. Stream audio to the browser as it arrives.

### Only if you want to show it on your phone
- [ ] **1.11 Deploy to Supabase and Railway** (both). Steps are in `docs/deploy.md`.
- [ ] **1.12 iPhone checklist** (Kate). Run `docs/iphone-checklist.md` in Safari and as a home-screen app.
- [ ] **1.13 Server speech-to-text** (both, optional). Choose OQ-8 and add `STT_API_KEY` if browser speech recognition isn't good enough.

### Submission
- [ ] **1.14 Record the demo video** (Kate).
- [ ] **1.15 Midway report** (Kate, Claude can draft sections). Problem spec, Environment & Data (the evaluation *plan*, using the archive numbers already in `docs/status.md`), Methods, Progress, References.
- [ ] **1.16 Merge into `main` and tag** (both, when Kate says go).
  - Fast-forward `main` to `claude/mvp-proposal-design-td19a9`. `main` is an ancestor, so there are no conflicts. Pick a pull request or a direct fast-forward.
  - Tag the submitted commit (e.g. `midpoint-demo`).
  - Keep `data/archive-raw` as its own branch.
  - Delete the merged `ws/*` branches, including the duplicate `ws/w-g`.
  - Make sure the graders can open the repo: make it public or add the course staff.

## Track 2 — Evaluation (frozen corpus and everything around it)

### Corpus
- [ ] **2.1 Wayback collection on the laptop** (Kate). Start early, it runs 35–70 h. Steps are in `eval/archive/README.md`.
- [ ] **2.2 Finish the cloud collection** (Claude, new session). Remaining: Hacker News comments and article bodies. The resume command is in `docs/status.md` §2.
- [ ] **2.3 Coverage check near brief time** (Claude), then **decide** (Kate): replay window (OQ-6, recommended 2026-09-12 → 2026-10-21) and whether Hacker News stays a signal (OQ-14).
- [ ] **2.4 Datasheet v0** (Claude), after the laptop run.
- [ ] **2.5 Hindsight labels** (Claude). Make `eval/archive/labels.py` call `eval.tasks.hindsight.build_labels` with the newsroom's clustering; add LLM topic labelling.

### Evaluation infrastructure and runs
- [ ] **2.6 Replay backends in the runner, then a newsroom replay over the whole window** (Claude).
- [ ] **2.7 Labelling** (Kate). Decide labelling time (OQ-4), then labelling session 1 on historical news.
- [ ] **2.8 Self-study** (Kate). Starts Sun Nov 15 and needs Track 1 working.
- [ ] **2.9 Model bake-off on the dev days, then pin `config/models.yaml`** (both). Kate decides the shortlist (OQ-16): keep late-cutoff models (DeepSeek V4.1, Qwen 3.8) out of the scoring roles, or run a leakage probe on them.
- [ ] **2.10 Validate the LLM judges against Kate's labels** (both).
- [ ] **2.11 Code freeze, then test runs** (Claude). Planned to launch Tue Nov 24.
- [ ] **2.12 Grading, failure analysis, final report** (both).
- [ ] **2.13 Remaining design decisions** (Kate). The defaults OQ-7 … OQ-23 in TDD §26.

### Clean-up
- [ ] **2.14 Spend cap is per process, not global** (Claude). Low priority while the daily `spend_guard` job exists.
- [ ] **2.15 Move tests to the shared fake model client** (Claude). Switch the newsroom and chief-of-staff local fakes to `app/llm/fake.py`.
