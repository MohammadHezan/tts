"""Deterministic fake ASR provider - no model weights, no I/O. Used by unit tests
and by the CLI harness's --dry-run mode to exercise the pipeline without a GPU.
"""

from __future__ import annotations

import numpy as np

from app.providers.base import AsrHypothesis, AsrProvider


class FakeAsr(AsrProvider):
    """Mimics the real provider's decode cadence (one hypothesis per ~0.5s of new
    audio) so pipeline/integration tests exercise realistic partial-event timing
    instead of one partial per network frame.
    """

    _CHUNK_SAMPLES = 8000  # ~0.5s @16kHz, matches config.yaml's default chunk_ms

    def __init__(self, final_text: str = "This is a fake transcript.", language: str = "en") -> None:
        self._final_text = final_text
        self._language = language
        self._started = False
        self._last_decoded_samples = 0

    def start_utterance(self, turn_id: str) -> None:
        self._started = True
        self._last_decoded_samples = 0

    async def feed(self, utterance_audio_so_far: np.ndarray) -> AsrHypothesis | None:
        if not self._started:
            raise RuntimeError("start_utterance() must be called before feed()")
        total_samples = len(utterance_audio_so_far)
        if total_samples - self._last_decoded_samples < self._CHUNK_SAMPLES:
            return None
        self._last_decoded_samples = total_samples
        # Grow the tentative text with each chunk, capped at the full final text.
        grown_len = min(len(self._final_text), 8 + 4 * (total_samples // self._CHUNK_SAMPLES))
        return AsrHypothesis(text=self._final_text[:grown_len], language=self._language, is_final=False)

    async def finalize(self, full_utterance_audio: np.ndarray) -> AsrHypothesis:
        if not self._started:
            raise RuntimeError("start_utterance() must be called before finalize()")
        self._started = False
        return AsrHypothesis(text=self._final_text, language=self._language, is_final=True)
