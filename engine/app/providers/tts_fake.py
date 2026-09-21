"""Deterministic fake TTS provider - no model weights, no I/O. Generates a
short sine-wave tone whose duration scales with text length, so tests and
the CLI/bench --dry-run path can exercise the AUDIO event path (latency
tracking, wire schema, CLI playback) without downloading Kokoro/Piper weights.
"""

from __future__ import annotations

import numpy as np

from app.providers.base import TtsAudio, TtsProvider

_SAMPLE_RATE = 16000
_TONE_HZ = 440.0


class FakeTts(TtsProvider):
    async def synthesize(self, text: str, lang: str) -> TtsAudio:
        duration_s = max(0.2, min(2.0, len(text) / 15))
        t = np.linspace(0, duration_s, int(_SAMPLE_RATE * duration_s), endpoint=False)
        tone = 0.2 * np.sin(2 * np.pi * _TONE_HZ * t)
        pcm16 = (tone * 32767).astype(np.int16).tobytes()
        return TtsAudio(pcm16=pcm16, sample_rate=_SAMPLE_RATE)
