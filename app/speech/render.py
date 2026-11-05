"""Brief pre-render (job `tts_render`): one short MP3 per brief sentence → AudioManifest.

`AudioSegment.url` holds the storage path (`{user_id}/{brief_id}/{sid}.mp3`); the API resolves it
to a playable URL (signed in prod) when serving the brief.
"""

from __future__ import annotations

import asyncio

from app.contracts.brief import AudioManifest, AudioSegment, BriefScript
from app.speech.base import TTSProvider
from app.speech.sentence_splitter import strip_citations
from app.speech.storage import AudioStorage


def audio_path(user_id: str, brief_id: str, sid: str) -> str:
    return f"{user_id}/{brief_id}/{sid}.mp3"


async def render_brief_audio(
    script: BriefScript,
    tts: TTSProvider,
    storage: AudioStorage,
    *,
    concurrency: int = 4,
    voice: str | None = None,
) -> AudioManifest:
    sem = asyncio.Semaphore(concurrency)

    async def one(sentence) -> AudioSegment:  # noqa: ANN001
        text, _ = strip_citations(sentence.text)
        async with sem:
            audio = await tts.synthesize(text, voice=voice)
        path = audio_path(script.user_id, script.brief_id, sentence.sid)
        await storage.put(path, audio.data, "audio/mpeg")
        return AudioSegment(
            sid=sentence.sid, url=path, duration_ms=audio.duration_ms, story_ref=sentence.story_ref
        )

    segments = await asyncio.gather(*(one(s) for s in script.sentences))
    return AudioManifest(brief_id=script.brief_id, segments=list(segments))
