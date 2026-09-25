"""Microsoft's neural voices (the ones Edge's Read Aloud uses), via edge-tts
(LGPL-3.0): natural-sounding Arabic and English at a conversational pace - the
local Piper Arabic voice is hard to follow. No account or key; needs internet,
which a meeting has anyway. It is Edge's own read-aloud service, used outside
Edge - Microsoft's official, keyed route for the same voices is Azure Speech.

Any sentence the service can't deliver in time is spoken by the local voices
instead (tts.provider multi_voice: Kokoro EN / Piper AR). After FAILURES_BEFORE_PAUSE
failures in a row it isn't tried again for PAUSE_S, so an outage costs one
timeout, not one per sentence.
"""

from __future__ import annotations

import asyncio
import io
import logging
import threading
import time
from collections.abc import Callable

import numpy as np
import soundfile as sf

from app.config import TtsConfig
from app.logging_utils import get_logger, log_event
from app.providers.base import TtsAudio, TtsProvider

FAILURES_BEFORE_PAUSE = 3
PAUSE_S = 60.0

# Sentences spoken by each kind of voice, and the last failure - for the
# dashboard's status (server._hardware_report), like asr_faster_whisper.last_loaded.
spoken = {"neural": 0, "local": 0}
last_error: str | None = None


def decode_audio(data: bytes) -> TtsAudio:
    """MP3 (what the service sends) or any other libsndfile format -> mono PCM16."""
    audio, sample_rate = sf.read(io.BytesIO(data), dtype="int16", always_2d=True)
    mono = audio.mean(axis=1).astype(np.int16) if audio.shape[1] > 1 else audio[:, 0]
    return TtsAudio(pcm16=mono.tobytes(), sample_rate=sample_rate)


class NeuralVoiceTts(TtsProvider):
    def __init__(self, cfg: TtsConfig, fallback_factory: Callable[[], TtsProvider] | None = None) -> None:
        self._cfg = cfg
        self._fallback_factory = fallback_factory
        self._fallback: TtsProvider | None = None
        self._fallback_lock = threading.Lock()
        self._failures = 0
        self._paused_until = 0.0
        self._logger = get_logger()

    async def synthesize(self, text: str, lang: str) -> TtsAudio:
        voice = self._cfg.neural.voices.get(lang)
        if voice and time.monotonic() >= self._paused_until:
            try:
                rate = self._cfg.neural.rates.get(lang, "+0%")
                audio = await asyncio.wait_for(self._neural(text, voice, rate), timeout=self._cfg.neural.timeout_s)
                self._failures = 0
                spoken["neural"] += 1
                return audio
            except Exception as error:  # the local voice speaks this sentence instead
                global last_error
                last_error = repr(error) if not isinstance(error, TimeoutError) else f"no answer within {self._cfg.neural.timeout_s:g}s"
                self._failures += 1
                if self._failures >= FAILURES_BEFORE_PAUSE:
                    self._paused_until = time.monotonic() + PAUSE_S
                log_event(self._logger, logging.WARNING, "neural_voice_failed", voice=voice, error=repr(error), failures=self._failures)
        fallback = await asyncio.to_thread(self._local)
        if fallback is None:
            raise RuntimeError(f"no voice for {lang!r}")
        audio = await fallback.synthesize(text, lang)
        spoken["local"] += 1
        return audio

    async def _neural(self, text: str, voice: str, rate: str) -> TtsAudio:
        import edge_tts

        data = bytearray()
        async for chunk in edge_tts.Communicate(text, voice, rate=rate).stream():
            if chunk["type"] == "audio":
                data += chunk["data"]
        if not data:
            raise RuntimeError("the voice service returned no audio")
        return await asyncio.to_thread(decode_audio, bytes(data))

    def _local(self) -> TtsProvider | None:
        # Loaded on first need: with the service working, the local models never load.
        with self._fallback_lock:
            if self._fallback is None and self._fallback_factory is not None:
                self._fallback = self._fallback_factory()
            return self._fallback
