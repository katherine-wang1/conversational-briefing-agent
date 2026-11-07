# web/ — iPhone web app (PWA) + interruptible audio engine (W-F)

React 18 + TypeScript + Vite + `vite-plugin-pwa`, Supabase auth, `@ricky0123/vad-web` (Silero VAD).
Design: TDD §13. Protocol: `app/contracts/session.py`, mirrored in `src/contracts/session.ts`.

## Run

```bash
cd web
npm install                 # also copies the VAD model + onnxruntime wasm into public/vad/

npm run dev:mock            # mock backend (:8787) + app (:5173), against canned data and fake audio
# or, separately:
npm run mock                # MOCK_PORT, MOCK_AUDIO_SCALE, MOCK_ANSWER_DELAY_MS, MOCK_ONBOARDED=false
npm run dev

BACKEND_URL=http://localhost:8000 npm run dev   # against the real backend (W-E)
```

The dev server proxies `/api`, `/ws`, `/audio` and `/media` (the real backend's local brief audio) to `BACKEND_URL`, so the app is same-origin. In dev, sign in with **Dev sign-in (bypass)**; it sends `Authorization: Bearer $VITE_DEV_TOKEN` (default `dev`). See `.env.example`. For the iPhone, see `docs/iphone-checklist.md` (HTTPS tunnel).

## Check

```bash
npm run typecheck && npm run lint && npm test && npm run build
npm run e2e          # Playwright smoke test against the mock (needs a global `playwright` + Chromium)
npm run e2e:prod     # same, against the production build
npm run check        # all of the above except e2e:prod
```

`npm run e2e` drives desktop Chromium with a fake microphone fed from `e2e/fixtures/question.wav` (synthesised speech, generated locally with ffmpeg's flite) and a stub `SpeechRecognition`. It checks: playback + 2 s position reports, tap interrupt with `{segment_id, offset_ms}`, VAD end of speech with no second tap (energy and Silero), browser and server STT, the answer audio queue, interrupting an answer, `resume_brief` back to the start of the interrupted sentence, skip, Media Session interrupt, headphones-mode barge-in, the rating sheet, and that every frame exchanged validates against the pydantic models in `app/contracts/session.py`.

## Layout

| Path | What |
|---|---|
| `src/audio/output.ts` | The single `HTMLAudioElement` (iOS unlock on first tap) |
| `src/audio/briefPlayer.ts` | Plays the `AudioManifest` segment by segment, preloads the next, reports position every 2 s |
| `src/audio/answerQueue.ts` | Plays streamed `tts_chunk`s in `seq` order |
| `src/audio/endpointer.ts` | End-of-speech logic (700 ms silence, 30 s cap, no-speech timeout, onset) |
| `src/audio/vad.ts` | Silero (vad-web) and energy voice detectors |
| `src/audio/browserStt.ts`, `mic.ts` | Web Speech STT; mic + MediaRecorder for server STT |
| `src/audio/mediaSession.ts` | AirPods / headphone / lock-screen controls → interrupt |
| `src/session/conversation.ts` | The client state machine: play, interrupt, listen, send, answer, resume (stage 1 and 2) |
| `src/session/socket.ts` | `/ws/session/{id}` with hello on (re)connect and backoff |
| `src/screens/*` | Login, Onboarding, Today, History, Memory, Open questions, Settings; `components/RatingSheet` |
| `mock/` | Mock backend: REST (TDD §11), WebSocket protocol, fake MP3 "TTS" |
| `e2e/smoke.mjs` | Playwright smoke test |

## Assumptions about the backend (for W-E)

- REST shapes not in `app/contracts/` are defined in `src/contracts/api.ts` (`Me`, `TodayBrief = {brief_id, status, script, manifest}`, `SessionCreated = {session_id, ws_url}`, `RatingRequest`, `OpenQuestion`, `HistoryItem`). The mock implements exactly these.
- WebSocket auth: browsers can't set headers on a WebSocket, so the bearer token is sent as `?token=` on the `ws_url`.
- Manifest segment URLs may be absolute or relative to the API host.
- Server STT: `utterance_audio.format` is `"webm/opus"` (Chrome) or `"mp4/aac"` (iOS Safari's MediaRecorder).
- `interrupt.segment_id` is `null` when the user interrupts an answer (the brief position was already reported).
- The client plays `resume_brief` only after the answer audio queue drains, after a 600 ms pause, from the start of the segment.
- Skip / Next act locally at once (next story / next sentence) and send `control`.
