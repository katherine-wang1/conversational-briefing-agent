# TTS spike: OpenRouter `openai/gpt-audio-mini` (2026-11-06)

**Verdict: pass, with caveats.** The model is now wired in as `TTS_PROVIDER=openrouter_audio`
(`app/speech/openrouter_audio.py`). Raw per-call numbers are in `measurements.json`.

## How it was called

- `POST https://openrouter.ai/api/v1/chat/completions`, `modalities: ["text","audio"]`,
  `audio: {voice: "alloy", format: "pcm16"}`, `stream: true`, `usage: {include: true}`.
- OpenRouter **requires `stream: true`** for audio output, and with streaming the upstream (OpenAI)
  **only accepts `pcm16`**. Asking for `mp3` returns HTTP 400: "does not support 'mp3' when stream=true.
  Supported values are: 'pcm16'". The adapter joins the base64 PCM chunks (24 kHz, mono, s16le) and
  wraps them in a WAV container. No new dependency is needed.
- Each delta carries `audio.data` (base64 PCM) and `audio.transcript` (what the model says it spoke).
  The final chunk carries `usage` with `cost` (USD) and `completion_tokens_details.audio_tokens`.

## Samples

- `p1`: brief story paragraph s002–s006 (OpenAI Images 2.6, "4 billion", "3 billion", "September").
- `p2`: paragraph s013–s015 (Ricursive / Ripple-65B, "80.52", "TerminalBench 2.1", "32 million dollars").
- Answer-style: `a1` "Here is the short answer: it has a 1-million-token context window." and
  `a2` "Good question. Moonshot says Kimi K3.1 Pro has a 1-million-token context window."
- Repeats: 3 runs each of the outro question, the intro, a1, a2 and s015, all with the strict prompt.
- End to end: the real `tts_render` handler on brief `br_01m6ndxnsgcert0max2e3m48nj`
  (docs/smoke/2026-11-05), 19 sentences, one call per sentence, 4 in parallel.

## Results against the pass criteria

| Criterion | Result | |
|---|---|---|
| Verbatim (no dropped/added sentences, names/numbers intact) | **Pass with the strict prompt; fail with a plain prompt** | see below |
| First audio for a short answer ≤ ~3 s | **Pass**: median 0.85 s, max 1.64 s (n=14). Full sentence returned: median 1.37 s, max 2.09 s | |
| Decodes in Chromium | **Pass**: 19/19 brief segments decode with Web Audio `decodeAudioData`, load in `<audio>` (`canPlayType('audio/wav')` = "maybe") and play. Durations match, 104.5 s total (Chromium 153, Playwright) | |
| Cost ≤ ~$0.01/min | **Pass**: $0.0042/min over the spike samples, $0.0044/min for the full brief | |

### Verbatim check

Checked two ways. (1) The model's own `transcript`. (2) Independent transcription of the audio by two
other models over OpenRouter (`google/gemini-2.5-flash` and `mistralai/voxtral-small-24b-2507`, audio in).

- **Plain prompt** ("You are a text-to-speech engine. Read the user's text aloud exactly as written…"):
  - `a1` **dropped the lead-in "Here is the short answer:"** on both tries. The transcript and both STTs agree.
  - On `a2`, the transcript said "Good question." but both STTs heard "And a/the question". So **the
    model's transcript is not proof of what the audio says.**
  - `p1` and `p2` were verbatim.
- **Strict prompt** (the one shipped: the text goes inside `<script>` tags, plus "read every word … including
  any greetings, lead-ins or questions … never reply to the script"):
  - 18/18 spike samples verbatim by transcript and by both STTs, including the outro question
    "What would you like to dig into?" (3/3, never answered).
  - Brief end to end: 19/19 sentences with no dropped or added sentences, and all names and numbers
    intact by both STTs (Images 2.6, 4 billion, 3 billion, September, Kimi K3.1 Pro, 1-million-token,
    3T-class, 80.52, TerminalBench 2.1, 32 million, one-tenth).
  - Remaining STT diffs are spelling or tokenisation only ("Open AI", "deep seek", "Kimmy K 3.1",
    "Recursive" for Ricursive, "$32 million"), with one exception below. Insert/delete word gap over
    268 words: Gemini 8, Voxtral 4. All of it is tokenisation, apart from that exception.
- **Pronunciation caveat:** "agentic coding" was heard as "agent at toting" / "H&T toting" by both STTs in
  3 of 6 runs of that phrase. In the end-to-end s014, "open-weight models" was heard as "open late models"
  by both STTs. The words were not changed, but they were not spoken clearly enough. Worth a listening test.

### Runaway with `temperature: 0` (found and fixed)

The first end-to-end attempt sent `temperature: 0`, and every sentence ran to the 16,384-token output
limit (about 130 s per call, $0.039 each). The spend fence stopped it after 4 calls ($0.158). A
controlled check showed the cause: at temperature 0 with `max_tokens: 300`, a 4.6 s sentence produced
13.7 s of audio, twice. With the default temperature it produced 4.65 s. **The transcript was correct
in both cases**, so a transcript check alone would not catch this. The adapter now:
(a) never sets temperature,
(b) sets `max_tokens = 100 + 20 × words` (about 2× headroom),
(c) rejects audio longer than 2 × the spoken-length estimate + 2 s, retrying once and then raising
`SpeechUnavailable`, and
(d) checks the ledger spend cap before every call.

### Tokens and cost

- Audio output: **1,200 audio tokens per minute** of speech, the same on every sample. With the
  transcript text included, about 1,530 completion tokens per minute.
- Prompt: about 100–120 tokens per call (system prompt + sentence). Text-in only; no audio-in tokens.
- Brief end to end: 19 calls, 2,097 tokens in, 2,667 tokens out, **$0.00766 for 104.5 s** = $0.0044/min.
  Wall time 7.7 s at concurrency 4.
- Per-sentence call: first audio median 876 ms (max 1,663 ms); whole call median 1.53 s.
- Paragraphs: p1 26.4 s of audio, first audio 0.87 s, done 3.74 s, $0.00169. p2 20.1 s, 0.86 s / 3.01 s, $0.00129.
- **Projection: 8 min/day ≈ $0.035/day ≈ $1.06/month** (plus about 100–150 prompt tokens per sentence,
  already included in the measured rate, and any retries).

### Format and Safari

The output is WAV (PCM 16-bit, 24 kHz mono). Safari on macOS and iOS plays PCM WAV natively in `<audio>`
and Web Audio, but **Safari could not be tested here**. The trade-off is size: 48 KB/s, so an 8-minute
brief is about 23 MB (about 160 MB in storage over the 7-day retention). A possible follow-up is
transcoding to MP3/AAC server-side (would need ffmpeg or an encoder in the image), about 8× smaller.

### Spend for this spike

About $0.19–0.23 in total: the $0.158 runaway, about $0.02 of TTS samples, the $0.0077 end-to-end
render, and about $0.01–0.04 of STT verification. The key's OpenRouter `usage_daily` read $0.232
afterwards, which is an upper bound.

## Files

- `s000_intro.wav`, `s014_story.wav`: the adapter's actual output (WAV) for two brief sentences.
- `brief_2026-11-05_full_listening_copy.mp3`: all 19 segments joined and transcoded to 48 kbps MP3
  locally, for listening only. The app stores WAV.
- `measurements.json`: per-call input, model transcript, both STT transcripts, timings, tokens, cost and the Chromium decode result.
