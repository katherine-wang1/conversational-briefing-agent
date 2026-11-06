"""TTS through an OpenRouter chat model with audio output (default `openai/gpt-audio-mini`).

It is a chat model, not a TTS engine, so two guards keep the audio faithful to the script:

- A strict system prompt with the text wrapped in `<script>` tags. In the 2026-11-06 spike
  (docs/smoke/2026-11-06/tts/results.md) a plain "read this verbatim" prompt dropped lead-ins such as
  "Here is the short answer:"; the strict prompt read 18/18 samples verbatim.
- The model also returns a transcript of what it spoke. If the transcript drops or adds words relative
  to the input, the sentence is re-synthesised once, then rejected with `SpeechUnavailable`.
- Runaway guard: never send `temperature: 0` (the spike showed the audio then never terminates while the
  transcript still looks right). `max_tokens` is bounded by the sentence length, and audio much longer
  than the spoken-length estimate is treated like a transcript mismatch (retry once, then reject).

OpenRouter only streams audio output, and only as `pcm16` (24 kHz, mono, 16-bit LE). The chunks are
joined and wrapped in a WAV container, which Chromium and Safari (incl. iOS) play natively.

Every call is written to the model-layer `Ledger` (role `tts`, provider-reported `usage.cost`), and the
ledger's spend cap is checked before each call.
"""

from __future__ import annotations

import base64
import difflib
import io
import json
import logging
import os
import re
import time
import wave
from typing import Any

import httpx

from app.llm.errors import SpendCapExceeded
from app.llm.ledger import JsonlLedgerSink, Ledger, LedgerRecord
from app.llm.openrouter import OPENROUTER_BASE_URL
from app.speech.base import SpeechUnavailable, TtsAudio, estimate_duration_ms

log = logging.getLogger(__name__)

DEFAULT_MODEL = "openai/gpt-audio-mini"
SAMPLE_RATE = 24_000  # pcm16 from OpenAI audio models: 24 kHz mono s16le

SYSTEM_PROMPT = (
    "You are a text-to-speech engine, not an assistant. The user message contains a script between "
    "<script> tags. Read every word of the script aloud exactly as written, from the first word to the "
    "last, including any greetings, lead-ins or questions. Never add, drop, reorder or paraphrase words, "
    "and never reply to the script. Output only the spoken script."
)


def pcm16_to_wav(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _words(s: str) -> list[str]:
    s = s.lower().replace("’", "'").replace("‘", "'")
    s = re.sub(r"[-‐-—/]", " ", s)
    s = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", s)
    return re.findall(r"[\w'.]+", s)


def transcript_gap(expected: str, spoken: str) -> int:
    """Number of words dropped or added in `spoken` vs `expected` (substitutions such as a homophone
    spelling, e.g. "Ricursive" vs "Recursive", count only by their length difference)."""
    a, b = _words(expected), _words(spoken)
    gap = 0
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op in ("delete", "insert"):
            gap += (i2 - i1) + (j2 - j1)
        elif op == "replace":
            gap += abs((i2 - i1) - (j2 - j1))
    return gap


def _default_ledger() -> Ledger:
    return Ledger([JsonlLedgerSink(os.environ.get("LLM_LEDGER_PATH", "eval/runs/llm_ledger.jsonl"))])


class OpenRouterAudioTTS:
    name = "openrouter_audio"

    def __init__(
        self,
        api_key: str | None,
        *,
        model: str = DEFAULT_MODEL,
        voice: str = "alloy",
        base_url: str = OPENROUTER_BASE_URL,
        client: httpx.AsyncClient | None = None,
        ledger: Ledger | None = None,
        timeout_s: float = 60.0,
        max_attempts: int = 2,
        max_word_gap: int = 1,
        app_title: str = "briefing-agent",
    ):
        if not api_key:
            raise SpeechUnavailable("OPENROUTER_API_KEY is not set")
        self.api_key = api_key
        self.model = model
        self.voice = voice
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.AsyncClient(timeout=timeout_s)
        self.ledger = ledger if ledger is not None else _default_ledger()
        self.max_attempts = max_attempts
        self.max_word_gap = max_word_gap
        self.app_title = app_title

    async def synthesize(self, text: str, *, voice: str | None = None) -> TtsAudio:
        text = text.strip()
        if not text:
            raise SpeechUnavailable("nothing to synthesise")
        max_ms = 2 * estimate_duration_ms(text) + 2000
        problem = ""
        for attempt in range(1, self.max_attempts + 1):
            pcm, transcript = await self._call(text, voice or self.voice, attempt)
            duration_ms = int(len(pcm) / 2 / SAMPLE_RATE * 1000)
            gap = transcript_gap(text, transcript) if transcript else 0
            if gap > self.max_word_gap:
                problem = f"transcript {gap} words off"
            elif duration_ms > max_ms:
                problem = f"audio {duration_ms} ms, expected at most {max_ms} ms"
            else:
                return TtsAudio(data=pcm16_to_wav(pcm), format="wav", duration_ms=duration_ms)
            log.warning("tts output rejected (attempt %d): %s", attempt, problem)
        raise SpeechUnavailable(
            f"TTS output did not match the script after {self.max_attempts} attempts ({problem})"
        )

    async def _call(self, text: str, voice: str, attempt: int) -> tuple[bytes, str]:
        try:
            self.ledger.check_cap()
        except SpendCapExceeded as e:
            raise SpeechUnavailable(str(e)) from e
        body: dict[str, Any] = {
            "model": self.model,
            "modalities": ["text", "audio"],
            "audio": {"voice": voice, "format": "pcm16"},
            "stream": True,
            # ~9 completion tokens per spoken word (audio ~1,200 tokens/min + transcript); ~2x headroom
            "max_tokens": 100 + 20 * len(text.split()),
            "usage": {"include": True},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"<script>{text}</script>"},
            ],
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "X-Title": self.app_title}
        t0 = time.perf_counter()
        ttfa: float | None = None
        chunks: list[bytes] = []
        transcript: list[str] = []
        usage: dict[str, Any] = {}
        error: str | None = None
        try:
            async with self.client.stream(
                "POST", f"{self.base_url}/chat/completions", headers=headers, json=body
            ) as r:
                if r.status_code != 200:
                    error = f"HTTP {r.status_code}"
                else:
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            ev = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if ev.get("error"):
                            error = "provider error in stream"
                            break
                        if ev.get("usage"):
                            usage = ev["usage"]
                        for ch in ev.get("choices") or []:
                            a = (ch.get("delta") or {}).get("audio") or {}
                            if a.get("data"):
                                if ttfa is None:
                                    ttfa = time.perf_counter() - t0
                                chunks.append(base64.b64decode(a["data"]))
                            if a.get("transcript"):
                                transcript.append(a["transcript"])
        except httpx.HTTPError as e:
            error = f"TTS request failed: {type(e).__name__}"
        pcm = b"".join(chunks)
        if error is None and not pcm:
            error = "no audio in response"
        self._record(usage, t0, ttfa, attempt, error)
        if error is not None:
            raise SpeechUnavailable(error if error.startswith("TTS") else f"TTS provider: {error}")
        return pcm, "".join(transcript)

    def _record(
        self, usage: dict[str, Any], t0: float, ttfa: float | None, attempt: int, error: str | None
    ) -> None:
        cost = usage.get("cost")
        self.ledger.record(
            LedgerRecord(
                kind="call" if attempt == 1 else "repair",
                role="tts",
                provider="openrouter",
                model=self.model,
                tokens_in=int(usage.get("prompt_tokens") or 0),
                tokens_out=int(usage.get("completion_tokens") or 0),
                cost_usd=float(cost or 0.0),
                cost_source="provider" if cost is not None else "none",
                latency_ms=int((time.perf_counter() - t0) * 1000),
                ttft_ms=int(ttfa * 1000) if ttfa is not None else None,
                stream=True,
                ok=error is None,
                error=error,
            )
        )
