"""Arabic TTS provider backed by Piper.

LICENSE NOTE: piper-tts is GPL-3.0-or-later, not Apache/MIT like the rest of
this stack's defaults - it's the only well-maintained, good-quality *offline*
neural Arabic TTS we found with a real Python API. It's used here as a
swappable provider (this whole file is the plug point), not statically linked
into the rest of the codebase. See README "Licensing" before shipping this
commercially; swap in a different Arabic backend here if your org requires
Apache/MIT-only dependencies.

Model weights are NOT bundled in the pip package - download an Arabic voice
separately (e.g. from the rhasspy/piper-voices collection) and point
config.yaml's tts.piper.model_path at it. See README "Setup".
"""

from __future__ import annotations

import asyncio

from app.config import TtsConfig
from app.providers.base import TtsAudio, TtsProvider


class PiperTts(TtsProvider):
    def __init__(self, cfg: TtsConfig) -> None:
        from piper import PiperVoice

        self._cfg = cfg
        self._voice = PiperVoice.load(cfg.piper.model_path, cfg.piper.config_path)

    async def synthesize(self, text: str, lang: str) -> TtsAudio:
        if lang != "ar":
            raise ValueError(f"PiperTts only supports Arabic (lang='ar'), got {lang!r}")
        return await asyncio.to_thread(self._synthesize_sync, text)

    def _synthesize_sync(self, text: str) -> TtsAudio:
        chunks = list(self._voice.synthesize(text))
        if not chunks:
            return TtsAudio(pcm16=b"", sample_rate=22050)
        pcm16 = b"".join(chunk.audio_int16_bytes for chunk in chunks)
        return TtsAudio(pcm16=pcm16, sample_rate=chunks[0].sample_rate)
