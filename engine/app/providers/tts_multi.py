"""Routes TTS synthesis to the right voice by language: Kokoro for English,
Piper for Arabic ("Kokoro for English; pluggable Arabic voice" from the spec).

Swap PiperTts for a different Arabic backend here without touching pipeline
code - this class is the whole plug point.
"""

from __future__ import annotations

from app.config import TtsConfig
from app.providers.base import TtsAudio, TtsProvider


class MultiVoiceTts(TtsProvider):
    def __init__(self, cfg: TtsConfig) -> None:
        from app.providers.tts_kokoro import KokoroTts
        from app.providers.tts_piper import PiperTts

        self._en = KokoroTts(cfg)
        self._ar = PiperTts(cfg)

    async def synthesize(self, text: str, lang: str) -> TtsAudio:
        if lang == "en":
            return await self._en.synthesize(text, lang)
        if lang == "ar":
            return await self._ar.synthesize(text, lang)
        raise ValueError(f"No TTS voice configured for lang={lang!r}")
