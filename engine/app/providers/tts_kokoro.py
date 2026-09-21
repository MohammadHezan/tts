"""English TTS provider backed by Kokoro (Apache-2.0), via the ONNX-runtime
build `kokoro-onnx` - no torch dependency.

Model weights are NOT bundled in the pip package (same situation as
faster-whisper's weights in Phase 1) - download them separately and point
config.yaml's tts.kokoro.model_path/voices_path at them. See README "Setup".
"""

from __future__ import annotations

import asyncio

import numpy as np

from app.config import TtsConfig
from app.providers.base import TtsAudio, TtsProvider


class KokoroTts(TtsProvider):
    def __init__(self, cfg: TtsConfig) -> None:
        from kokoro_onnx import Kokoro

        self._cfg = cfg
        self._kokoro = Kokoro(cfg.kokoro.model_path, cfg.kokoro.voices_path)

    async def synthesize(self, text: str, lang: str) -> TtsAudio:
        if lang != "en":
            raise ValueError(f"KokoroTts only supports English (lang='en'), got {lang!r}")
        audio_f32, sample_rate = await asyncio.to_thread(
            self._kokoro.create,
            text,
            voice=self._cfg.kokoro.voice,
            lang=self._cfg.kokoro.lang,
        )
        pcm16 = (np.clip(audio_f32, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        return TtsAudio(pcm16=pcm16, sample_rate=sample_rate)
