# Playing screen: speed, citations, talk button, live log (TODO 1.21, 1.22, 1.25, 1.26): 2026-11-07

**Result: PASS (14/14 live checks).** Headless Chromium against the real backend. The brief and the
answers play at 1.2× with pitch preserved, and the speed stays set after every segment change. The
"Tap to talk" button interrupts. Answers and History show source names that link to the articles.
The live log shows client and server events. Settings persist on the server.

## Setup
- Fresh Postgres database `briefing_ui` (container `wse-pg`, all migrations), `uvicorn app.main:app`,
  `python -m app.jobs.worker`, `BACKEND_URL=http://localhost:8000 vite`.
- Real providers: `AGENT_PORTS=auto`, `TTS_PROVIDER=openrouter_audio` (WAV), `SEARCH_PROVIDER=tavily`,
  `DEV_AUTH_BYPASS=1`, `LLM_SPEND_CAP_USD=1`. There is no server STT. A stub `SpeechRecognition`
  returns the question, as in the 2026-11-06 run.
- To save searches, `config/agent.yaml` had `cos_daily.searches: 5` locally (not committed).
- The built-in dev user was onboarded through the API. After the 3 bootstrap newsroom runs, the
  first brief had 5 stories and 18 segments (122 s of audio).
- Driver: `driver/run.mjs`. Raw results: `run.json`. Screenshots: `shots/` at 390×844 and 1280×800.

## Checks
| Step | Result |
|---|---|
| Settings: Voice speed 1.3× and "Show what's happening" on → `PATCH /api/me {settings: {playback_rate, show_debug}}` | stored; after clearing local storage and reloading, both come back from `/api/me` (`shots/01`) |
| Header estimate (1.25) | "5 stories · ~1.5 min": the manifest has 122 s, which is 102 s at 1.2× (the script estimate was 117 s) (`shots/02`) |
| Brief at 1.2× | `playbackRate` 1.2 and `preservesPitch` on every 1-second sample over 2 segments (`shots/03`) |
| "Tap to talk" button | paused within 0 ms; listening state (`shots/04`) → thinking (`shots/05`) → speaking (`shots/06`) → resuming (`shots/07`) → playing |
| Answer audio at 1.2× | yes, on the same element |
| Readable citations (1.22) | `transcript.sources`: `anthropic.com`, "Introducing Claude Haiku 5.5" / "Introducing Claude Opus 5.5" with https links. The chips show those names and no passage ids. History shows the same chips (`shots/11`) |
| Live log, phone | slide-up drawer (`shots/08`). Client entries: ws, out, in, audio, latency, stt. Server entries: state, heard, model, speak, tts, memory, info |
| Live log, desktop | side panel next to the player (`shots/09`, `shots/10`) |
| Model entry | `voice.turn round 1: anthropic/claude-haiku-5.5 · 2817 ms (first token 1079 ms) · 13317 in (0 cached) / 146 out · $0.0017` |

Latency is unchanged (see 1.20): end of speech → first answer audio took 3.2 s, and 2.5 s for the
second question.

These paths were not triggered live and are covered by unit tests instead
(`tests/unit/platform/test_voice_debug_citations.py`): quick_search entries (query, hit count, time)
and search hits as citations, and the `drop` entry for a meta sentence.

## Usage
- Model + TTS: **$0.075** (ledger). Newsroom: $0.045. Brief (`cos_*`): $0.017. TTS: $0.010. Two voice
  turns: $0.002. Memory: $0.001.
- Tavily: **5 searches**, all in `cos_daily`.

## Not tested here
- iPhone Safari: `playbackRate` with `webkitPreservesPitch` on WAV, the rate after background `src`
  switches with the screen locked, and how the drawer looks in a home-screen app with a safe area
  (`docs/iphone-checklist.md` A6, B3).
- Real speech input (the STT was stubbed) and AirPods.
