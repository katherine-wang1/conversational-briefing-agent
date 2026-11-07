# iPhone checklist (W-F audio engine)

Manual checks for the web app on a real iPhone, in **Safari** and as a **home-screen app**. They cover what desktop Chromium and the Playwright smoke test (`web/e2e/smoke.mjs`) can't: iOS audio sessions, AirPods, Web Speech on iOS, routing and screen lock. The 🔬 items in TDD §13.2 are answered here.

## Setup (once)

1. On the laptop, start the mock backend and the app:
   ```bash
   cd web
   npm install
   npm run dev:mock          # mock server :8787 + Vite :5173
   ```
   To test against the real backend instead, start W-E's API and run `BACKEND_URL=http://localhost:8000 npm run dev`.
2. The microphone only works over **HTTPS** on the phone. Expose the dev server through a tunnel:
   ```bash
   cloudflared tunnel --url http://localhost:5173     # or: ngrok http 5173
   ```
   Open the `https://….trycloudflare.com` URL on the phone. (`*.trycloudflare.com` and `*.ngrok*.app` are already in Vite's `allowedHosts`; for other hosts set `ALLOWED_HOSTS=`.)
3. Sign in with **Dev sign-in (bypass)** (mock) or a magic link (real backend).
4. Home-screen app: in Safari, Share → **Add to Home Screen**, then open it from the icon. Run the checklist once in Safari and once in the home-screen app.
5. Open **debug** (bottom right of Today) to read the timings: `interrupt→pause` and `end-of-speech→audio`.

Useful URL switches (add to the URL before `#/today`; not saved): `?stt=server`, `?stt=browser`, `?vad=energy`, `?silence=900`, `?headphones=1`. The same knobs are under Settings → Advanced.

Record the iOS version, iPhone model and headphones used at the top of the results table.

## Checks

### A. Playback
1. Tap the microphone character ("Play today's brief"). The brief starts within ~1 s, with no "tap to allow audio" prompt.
2. Let it run across at least 3 segments. There is no audible gap or click longer than ~300 ms between sentences.
3. The story card follows the audio (headline changes at each new story).
4. Skip story jumps to the next story; Next jumps one sentence.
5. Lock-screen / Control Centre shows "Briefing" with the story headline.
6. Speed: the brief plays at 1.2× by default and the voice sounds natural (not chipmunk). Tap the `1.2×` chip to change it; it stays across sentences, answers and a reload (Settings → Voice speed shows the same value).

### B. Tap interrupt
1. While the brief plays, tap anywhere in the lower half. Audio stops **instantly** (live log `interrupt → brief paused` < 50 ms with Settings → "Show what's happening" on; by ear, no trailing syllable).
2. The first time, iOS asks for microphone permission. Allow it. The app keeps working afterwards without a reload.
3. The character turns red ("Listening…") with sound waves. Ask a question, then stop talking. It turns purple while thinking and green while speaking; citations under the answer show source names that open the article.
4. Tap the "Tap to talk" button instead of the area: same behaviour.
5. Deny the mic permission once (Settings → Safari → Microphone → Deny, then retry): the app shows a clear message and returns to the brief; it doesn't hang.

### C. AirPods / headphone controls
1. With AirPods connected and the brief playing, **squeeze** (AirPods Pro / 3rd gen) or tap (older). The brief pauses and the app starts listening.
2. Speak; the answer plays; the brief resumes.
3. While the **answer** plays, squeeze again: the answer stops and the app listens.
4. While listening, squeeze: this ends the utterance immediately (manual "done").
5. Repeat 1 with wired EarPods (centre button) if available.
6. Note whether the squeeze still works **after** the mic has been opened once (iOS may hand the media session to the recording; this is the main 🔬 risk).

### D. End of speech (VAD)
1. Interrupt and ask a ~3-second question. The app moves to "Thinking…" **without a second tap**, about 0.7 s after you stop.
2. Ask a question with a natural mid-sentence pause (~0.5 s). It must **not** cut you off.
3. Interrupt and stay silent. After ~8 s it says "Didn't catch that" and resumes the brief.
4. Talk for over 30 s. It stops listening at 30 s and sends what it has.
5. If cut-offs are frequent, try Settings → Advanced → silence 900–1000 ms and note the value that works.
6. Note the debug `vad=` value: `silero` expected; `energy` means the Silero model failed to load (record the notice text).

### E. Web Speech accuracy (browser STT)
1. With STT = Browser, ask these five questions and note the transcript shown in the bubble:
   - "Who funded that?"
   - "Which models held up best in the long-context paper?"
   - "Go back to the battery story."
   - "Does that hold for Atlas-3 too?"
   - "What do you know about me?"
2. Count words wrong per question.
3. Repeat with STT = Server (real backend only; the mock always "hears" a fixed question).
4. Note whether `webkitSpeechRecognition` exists in the **home-screen app** (if not, the app falls back to server STT automatically and shows a notice).

### F. Audio routing and volume when the mic opens
1. **Phone speaker, no headphones:** play the brief at a comfortable volume, interrupt, ask, and listen to the answer. Is the answer as loud as the brief? Does it come out of the loud speaker (bottom) or the earpiece (top)?
2. After the answer, the brief resumes: is the volume back to normal?
3. **AirPods:** same. Do they switch to the low-quality "call" profile while the mic is open (audio sounds muffled / mono), and do they switch back after?
4. Note any delay or click when the mic opens or closes.

### G. Screen lock and background
1. Start the brief, then lock the phone. Does audio continue to the next segment? (Expected: iOS may stop at the next segment boundary in a PWA; record what happens.)
2. With the phone locked, use the lock-screen pause control: it should interrupt (mic) or pause.
3. Switch to another app for 10 s and come back. Does the session reconnect and continue (or show a clear state)?
4. Leave it on the Today screen for 2 minutes with the screen on, not playing. Then tap ▶: it still plays (no lost audio unlock).

### H. Stage 2 (headphones mode, optional)
1. Settings → Headphones mode on, AirPods in.
2. Play the brief and just start talking (no tap). It should interrupt within ~0.3 s.
3. Listen for false interrupts from the brief's own audio (should be none with headphones).
4. Note playback volume/quality while the mic stays open (expected to be worse; this is OQ-20).

## Results

Device: ______ · iOS: ______ · Headphones: ______ · Date: ______ · Backend: mock / real

| # | Check | Safari: pass / fail | Home-screen app: pass / fail | Notes (timings, transcripts, what you heard) |
|---|---|---|---|---|
| A1 | Play starts, no prompt | | | |
| A2 | Gaps between segments | | | |
| A3 | Story card follows audio | | | |
| A4 | Skip / Next | | | |
| A5 | Lock-screen metadata | | | |
| B1 | Tap interrupt is instant | | | interrupt→pause = ___ ms |
| B2 | Mic permission prompt | | | |
| B3 | Listening state | | | |
| B4 | Button interrupt | | | |
| B5 | Mic denied handled | | | |
| C1 | AirPods squeeze interrupts brief | | | |
| C2 | Answer, then resume | | | |
| C3 | Squeeze interrupts answer | | | |
| C4 | Squeeze ends utterance | | | |
| C5 | Wired headphones button | | | |
| C6 | Squeeze still works after mic used | | | |
| D1 | Auto end of speech | | | end-of-speech→audio = ___ ms |
| D2 | Mid-sentence pause not cut | | | |
| D3 | Silence timeout | | | |
| D4 | 30 s cap | | | |
| D5 | Best silence setting | | | ___ ms |
| D6 | VAD engine | | | silero / energy |
| E1 | Q1 "Who funded that?" | | | words wrong: |
| E1 | Q2 long-context models | | | words wrong: |
| E1 | Q3 battery story | | | words wrong: |
| E1 | Q4 Atlas-3 | | | words wrong: |
| E1 | Q5 about me | | | words wrong: |
| E3 | Server STT (real backend) | | | |
| E4 | Web Speech in home-screen app | | | present / absent |
| F1 | Speaker: answer volume / speaker vs earpiece | | | |
| F2 | Volume restored after mic closes | | | |
| F3 | AirPods call profile | | | |
| F4 | Clicks / delay on mic open/close | | | |
| G1 | Audio continues when locked | | | |
| G2 | Lock-screen control | | | |
| G3 | Background and return | | | |
| G4 | Unlock persists while idle | | | |
| H2 | Voice barge-in | | | |
| H3 | False barge-ins | | | |
| H4 | Playback quality with mic open | | | |

## If something fails

- **Tap interrupt needs two taps or audio won't start:** Settings → Advanced → "Interrupt on: Tap release" and retry B1 (iOS user-activation rules differ between touch-down and tap).
- **Browser STT returns nothing in the home-screen app:** switch to STT = Server (needs the real backend's STT).
- **Answers come out of the earpiece / are quiet after the mic opens:** record it in F1 — that is the iOS "play-and-record" audio session. The app already closes the mic after each utterance (stage 1); if it persists, this is evidence for OQ-20.
