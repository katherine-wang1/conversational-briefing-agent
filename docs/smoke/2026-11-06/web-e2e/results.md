# Web app against the real backend, with sound (TODO 1.3): 2026-11-06

**Result: PASS after fixes.** A new user signed in, onboarded, waited for the first brief, played it
with real audio, interrupted, asked (stubbed browser speech and typed), heard the spoken answer,
went back to the brief, played to the end, ended the session, and checked History, Memory,
Questions, Settings and a reload. Ten bugs were found and fixed (list below). Every step passed
on the final code.

## Setup
- Headless Chromium (Playwright), 390×844, timezone America/Los_Angeles, `--autoplay-policy=no-user-gesture-required`, fake mic device.
- Postgres 16 (`briefing_e2e`, fresh, all migrations), `uvicorn app.main:app` (API + WS), `python -m app.jobs.worker`, `vite` dev with `BACKEND_URL=http://localhost:8000`.
- Real providers: `AGENT_PORTS=auto` (real newsroom / chief of staff / voice, Haiku 5.5 via OpenRouter), `TTS_PROVIDER=openrouter_audio` (WAV), `SEARCH_PROVIDER=tavily` (key injected by the proxy), `AUDIO_STORAGE=local`, `LLM_SPEND_CAP_USD=1`, `DEV_AUTH_BYPASS=1`. No server STT (`STT_PROVIDER` empty).
- Test user `kate-e2e@example.com` (invited, signed in through the dev bypass with `VITE_DEV_TOKEN=dev:<uuid>:<email>`). The first attempt used the built-in dev user; it found the onboarding race (bug 4).
- For the second brief only, the `cos_daily` search budget was set to 6 in `config/agent.yaml`. That change was local and is not committed.
- Driver scripts: `driver/` (step1–step6). Raw results: `runs/`. Screenshots: `shots/`.

## Steps
| # | Step | Result | What happened |
|---|---|---|---|
| 0 | Fresh deploy: shared news exists | **PASS after fix** | At 00:00 UTC the latest beat slot (18:00) was more than 3 h old, so the scheduler queued no `newsroom_run`, and a first brief would have had no digest at all. With bug 1 fixed, all 3 beats ran at once (ai 30, energy 29, physical_ai 25 entries, 3–4.5 min each). The first runs also hit bugs 2 and 3. |
| 1 | Sign in → onboarding; brief before onboarding | **PASS after fix** | Dev sign-in → `/api/me` `onboarded:false` → `#/onboarding` (`shots/01`). Before onboarding, `GET /api/briefs/today` returns **409** `{"status":"onboarding_required"}`. The app used to show "Couldn't load today's brief: ApiError …". It now treats 409 as `onboarding_required` and goes to onboarding. `POST /api/onboarding` returns **202**. The real extraction stored 4 interests in 2.7 s ("AI agents", "agent memory research", "grid-scale battery storage", "humanoid robots"). |
| 2 | "Preparing your brief" → ready | **PASS after fix** | `GET /api/briefs/today` → 202 `{status:"pending", brief_id:null}`, polled every 4 s, banner shown (`shots/02`). `cos_daily` waited about 3 s for `onboarding_seed` (bug 4) and finished in 104 s with 6 live searches. `tts_render` made 22 WAV segments (7.2 MB) in 10 s. The Today screen was ready **111 s** after it opened (`shots/03`). Brief: 6 stories: 2 big news (digest: Anthropic S-1, Claude 5.5 launch), 3 for you (Agility humanoid IPO from the digest; India 92 GWh storage and Perplexity agents from search), 1 outside your usual (China blocks SoftBank/ABB Robotics). |
| 3 | Play with real audio | **PASS** | Segments load from `/media/…/sNNN.wav` (`audio/x-wav`, 24 kHz mono PCM) and play in order. Over the whole run: 28 `playing`, 26 `ended`, **0 `error`** events. `currentTime` advanced (1668 → 3171 ms in 1.5 s). Media Session metadata was set (title = story headline, artist "Your briefing", state `playing`) (`shots/04`). WebSocket `hello` → `READY` → `PLAYING_BRIEF`. |
| 4 | Interrupt → ask → hear the answer → resume → end | **PASS after fixes** | The Interrupt button paused at once (0 ms) and sent `interrupt{segment_id:"s001", offset_ms:3259}`. The phase went to listening (`shots/05`). **STT:** headless Chromium has no Web Speech backend and no server STT is configured, so a stub `SpeechRecognition` returned the question text (the same stub as `web/e2e`); the second tap ended the utterance. Typed questions ("Type instead") were also tested, 6 times. Answer: 3 `tts_chunk`s (WAV) played through the answer queue. End of speech → first answer audio was **3.0 s** (typed: 2.5–4.3 s). The target is ≤ 1 s; that is TODO 1.20. Then `resume_brief{segment_id:"s001"}` played from the start of the interrupted sentence (`shots/06`). The brief played to the end: the client sent `playback.completed` (bug 6), the server reached `OPEN_FLOOR`, and the app showed open floor (`shots/07`). End → `ENDED`, rating sheet (`shots/08`). Session `end_reason = natural`; `memory_update` job done; turns stored (`interrupt`, user question, agent answer with 2 citations). Rating: `POST /api/ratings` with the app's body → 201. A bookmarked question ran `followup_research` (status `unanswerable`). |
| 5 | History | **PASS after fix** | `/api/history` returns `{briefs, sessions}`; the app expected `HistoryItem[]` and crashed on `.map`. It now shows one card per brief with headlines, and expanding a card loads the session transcript from `/api/sessions/{id}/transcript` (`shots/09`). The Memory (`{interests}`, `shots/10`) and Questions (`{questions}`) screens had the same wrapper mismatch and work now. |
| 6 | Reload / come back later | **PASS after fix** | After a reload, the brief loads at once (312 ms, no new `cos_daily`). **Progress:** before the fix a reload restarted the brief from sentence 1. Now the device remembers the sentence: after 2 skips (segment 5) and a reload, the button reads "Resume today's brief" and playback restarts at `s005` (`shots/11`). Settings: changing STT mode now reaches the server (bug 9) and survives a reload. |

## Bugs fixed
1. **No digest on a fresh deploy** (`app/jobs/scheduler.py`). `newsroom_run` was only queued within 3 h of a beat slot, so after a deploy at most hours of the day the first brief had no shared news. Now a beat with no digest in the last 24 h runs its latest slot at once.
2. **Every `newsroom_run` failed** (`app/db/repos.py`: `headline_observations.extra` was not in `json_cols`): `cannot adapt type 'dict'`. The live adapters set `extra` (feed position, techmeme cluster, …).
3. **Docker image lacked the newsroom extra** (`Dockerfile`). Locally, `newsroom_run` failed with "fastembed is not installed". The image installed only `pip install -e .`, so on Railway every newsroom run would have failed. The image now installs `.[newsroom]` and bakes the `bge-small-en-v1.5` model in at `FASTEMBED_CACHE_PATH=/srv/var/fastembed`. Checked with a local `docker build` of the runtime stage (with the sandbox CA added): embedding as `appuser` works from the cache in 0.27 s.
4. **First brief built before onboarding stored the interests** (`app/jobs/handlers.py`). The app asks for today's brief right after `POST /api/onboarding`, so `cos_daily` was claimed 2 ms after `onboarding_seed`. In the first attempt it loaded memory before the interests existed and built a generic brief (0 searches, nothing "for you"). `cos_daily` now waits for a pending `onboarding_seed` of the same user (up to 2 min).
5. **Voice answers spoke the model's reasoning** (`app/agents/voice/context.py`, `session.py`, `prompts/voice.turn.md`). Haiku with reasoning off said things like "The listener is asking about China blocking a deal, and their position is story 2…", "I should answer…", and "The corpus doesn't name…" (`runs/step6-typed-before-fix.json`). The fix has three parts: the prompt says everything is spoken (v2); each turn ends with a one-line reminder; and sentences that narrate the reasoning are dropped before TTS, while "the corpus" becomes "what I have". In the run after the fix, one meta sentence was dropped and logged, and none was spoken (`runs/step6d…`, `step6e…`).
6. **The client never told the server that the brief ended** (`web/src/audio/briefPlayer.ts`). The backend contract has `playback.completed` (W-E, additive). The web mirror lacked it, which also made `src/contracts/session.test.ts` fail on main. Without it the server never left `PLAYING_BRIEF`, and every session ended `cut_short`, which feeds memory the wrong signal. The client now sends `completed:true` for each segment that finished.
7. **REST shape mismatches** (`web/src/lib/normalize.ts`, `api.ts`): `/api/history`, `/api/memory/interests` (`{interests}`), `/api/questions` (`{questions}`, with the answer under `answer.answer_sentences`), and `GET /api/briefs/today` 409 / 202 with `brief_id: null`. These are adapted on the client; the backend is unchanged.
8. **WebSocket URL** (`web/src/lib/api.ts` `resolveWsUrl`). The backend builds `ws_url` from its own view of the host (`ws://localhost:8000/...` behind the Vite proxy, and the same behind an HTTPS tunnel to the phone). With no `VITE_API_BASE`, the app now uses the path on its own origin (`ws://localhost:5173/ws/...` here, `wss://` behind a tunnel or Railway).
9. **Settings never saved** (`mePatchToApi`). The app sent `PATCH /api/me {stt_mode: …}` flat; the backend wants `{settings: {...}}` and ignored the field, so every reload reset the settings from the server.
10. **Dev proxy didn't forward `/media`** (`web/vite.config.ts`). With `AUDIO_STORAGE=local`, brief audio is served at `/media`; the Vite dev and preview servers now proxy it, which the iPhone checklist needs.

Also: the brief position is saved per device (`web/src/session/progress.ts`, step 6), and the mock server (`web/mock`) now returns the real API shapes (409/202/201, `{briefs, sessions}`, transcripts, `{interests}`, `{questions}`, nested PATCH `/api/me`, absolute `ws_url`), so `npm run e2e` exercises the adapters.

## Not fixed (new TODO items)
- **Raw passage ids as citations.** Answers and History show citations as `138f8fb…:192-436`. Show the source name and link instead (the source packs have the URL).
- **The voice model is too eager to bookmark.** It said "I can bookmark that…" and saved the question in the same answer, without waiting for a yes.
- **No STT fallback on the phone.** `STT_PROVIDER` is empty. If iOS Web Speech fails (it is known to be flaky in home-screen apps), only typing works (see 1.13).
- `~2 min` was shown for 22 sentences, but the audio is about 2.5 min at the current voice speed (1.21 will change it anyway).

## iPhone / deploy risks (not testable here)
- **WAV on iOS**: 24 kHz 16-bit PCM WAV with `audio/x-wav` (local) should play in Safari. Supabase Storage serves it with the content type set at upload. Check it on the phone (checklist).
- **Autoplay / audio session**: the first play is a tap (unlock). Later segments and answer audio play on the same element without a gesture, and that worked in Chromium. On iOS, test locking the screen mid-brief: switching `src` in the background may stall.
- **Signed URLs** (Supabase): segment URLs expire after 6 h. They are signed on each `GET /api/briefs/today`, so reopening the app fixes them, but a tab left open over 6 h will skip unplayed segments. Segments are fetched (CORS) into blob URLs, so Supabase must allow the app origin (it allows `*` by default).
- **`PUBLIC_BASE_URL`**: set it on Railway. With `VITE_API_BASE` empty (one service), the app now ignores the host in `ws_url`, so the `wss://` scheme comes from the page.
- **First login timing**: after a fresh deploy, wait for the 3 bootstrap newsroom runs (3–5 min) before onboarding, or the first brief has search stories only (`docs/deploy.md` §2.5).
- **Invite**: Kate's email must be in `invites` (deploy.md §1.9), or sign-in returns 403 and the app shows "Can't reach the server: … not_invited".
- **Brief audio size**: 7.2 MB for 6 stories (WAV). Fine on Wi-Fi; 1.19 (MP3) cuts it about 8×.

## Usage
- **Model + TTS: $0.18** from the Router ledger. Newsroom 3 beats $0.081; 2 `cos_daily` runs $0.047 (one aborted); TTS $0.035 (2 briefs + 7 answers); voice turns $0.008; memory/onboarding/follow-up $0.007.
- **Tavily: 19 searches.** 11 went to the first `cos_daily` that ran during the race (bug 4) and was then discarded; 6 to the final brief; 2 to `followup_research`. No quick searches during voice turns.

## Checks after the fixes
`ruff check .` clean; `pytest`: 557 passed, 3 skipped. Web: `typecheck`, `lint`, `vitest` (37 tests), `build`, and `npm run e2e` against the mock (35/35) all pass.
