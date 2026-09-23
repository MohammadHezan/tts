"""asr.candidate_languages: auto-detect only ever picks English or Arabic."""

from __future__ import annotations

import numpy as np

from app.config import AsrConfig
from app.providers.asr_faster_whisper import FasterWhisperAsr


class _FakeWhisperModel:
    def __init__(self, probs: list[tuple[str, float]]) -> None:
        self.probs = probs

    def detect_language(self, audio: np.ndarray) -> tuple[str, float, list[tuple[str, float]]]:
        return self.probs[0][0], self.probs[0][1], self.probs


def _asr(cfg: AsrConfig, probs: list[tuple[str, float]]) -> FasterWhisperAsr:
    asr = FasterWhisperAsr.__new__(FasterWhisperAsr)  # skip loading a real model
    asr._cfg = cfg  # noqa: SLF001
    asr._model = _FakeWhisperModel(probs)  # noqa: SLF001
    return asr


AUDIO = np.zeros(16000, dtype=np.float32)
# Whisper's top guess for Jordanian Arabic on a short clip is sometimes Persian.
PERSIAN_FIRST = [("fa", 0.55), ("ar", 0.35), ("en", 0.05), ("ur", 0.05)]


def test_picks_the_likeliest_candidate_not_the_likeliest_language() -> None:
    cfg = AsrConfig(language="auto", candidate_languages=["en", "ar"])
    assert _asr(cfg, PERSIAN_FIRST)._resolved_language(AUDIO) == "ar"  # noqa: SLF001


def test_no_candidates_leaves_detection_to_whisper() -> None:
    assert _asr(AsrConfig(language="auto"), PERSIAN_FIRST)._resolved_language(AUDIO) is None  # noqa: SLF001


def test_fixed_language_wins() -> None:
    cfg = AsrConfig(language="en", candidate_languages=["en", "ar"])
    assert _asr(cfg, PERSIAN_FIRST)._resolved_language(AUDIO) == "en"  # noqa: SLF001
