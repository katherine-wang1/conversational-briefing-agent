# W-F: iPhone web app (PWA) + interruptible audio engine

**Branch:** `ws/web` (from `claude/mvp-proposal-design-td19a9`)
**Owns:** `web/`, `docs/iphone-checklist.md`
**Contract changes:** none. Mirror `app/contracts/session.py` in TypeScript.

## Read first
- `docs/parallel-work.md`
- `docs/02-technical-design.md` §11 (API), §12 (voice session + WebSocket protocol), §13 (frontend, audio engine, two-stage interruption)
- `docs/01-PRD.md` §5 (UX) and VOICE-7 / VOICE-8
- `app/contracts/session.py`

## Stack
React 18 + TypeScript + Vite + `vite-plugin-pwa`; `@supabase/supabase-js` for auth; `@ricky0123/vad-web` for voice activity detection. Mobile-first: it must work in **iPhone Safari** and as a home-screen app.

## Build
1. **Screens**
   - Login: Supabase magic link + Google, plus a dev bypass.
   - Onboarding: free-text interests, length and depth prefs, brief time, timezone, research consent.
   - **Today:**
     - a big play button;
     - the current story card;
     - a live transcript;
     - a large talk / interrupt area covering the lower half of the screen;
     - skip and next buttons;
     - an open-questions badge.
   - Post-session rating sheet: usefulness 1–5, per-story relevance, "learned something new", length fit, follow-up usefulness.
   - History.
   - Memory: the interests list, with edit, pin and delete.
   - Open questions.
   - Settings: brief time, voice, STT mode (browser / server), auto-resume, headphones mode.
2. **Audio engine, stage 1 (the core of this workstream)**
   - **Playback:** play the brief from an `AudioManifest` (ordered short MP3 segments) through a **single HTMLAudioElement**, preloading the next segment. Report `playback` every 2 s.
   - **Interrupt instantly** by any of:
     - a tap anywhere in the talk area;
     - the on-screen button;
     - **AirPods or headphone controls**, through the Media Session API (`setActionHandler('pause'|'play')` mapped to interrupt).
   - **On interrupt:**
     1. pause, and record `{segment_id, offset_ms}`;
     2. open the microphone (`getUserMedia` with echoCancellation + noiseSuppression);
     3. run VAD and **detect the end of speech automatically** (about 700 ms of silence, configurable; max 30 s), with no second tap.
   - **STT:**
     - browser mode: `webkitSpeechRecognition`, started on interrupt, with the final result taken at VAD end;
     - server mode: send `utterance_audio` (webm/opus).
   - **Answers:** play streamed `tts_chunk`s through an audio queue. Answers can be interrupted the same way. Handle `resume_brief`.
   - **iOS:** unlock audio on the first tap; handle permission prompts and audio-session quirks gracefully.
3. **Stage 2** (lower priority; behind the "headphones mode" setting): keep the microphone open during playback, with echo cancellation, and treat voice onset (≥ 250 ms of voice) as an interrupt.
4. **Mock session server** (`web/mock/`): a small Node WebSocket server that speaks the protocol, with canned briefs, answers and fake TTS audio. Use it for development without the backend.
5. **`docs/iphone-checklist.md`:** the manual steps Kate runs on her phone, in Safari and as a home-screen app:
   - playback;
   - tap interrupt;
   - AirPods squeeze;
   - VAD end-of-speech;
   - Web Speech accuracy;
   - audio routing and volume when the microphone opens;
   - screen-lock behaviour.

   Include a results table for her to fill in.

## Done when
- `npm run build` and the typecheck pass.
- The interrupt → speak → answer → resume flow works in desktop Chrome against the mock server, with a Playwright smoke test.
- The checklist is written.
