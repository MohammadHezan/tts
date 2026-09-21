"""Streaming speech endpointing on top of Silero VAD.

Accepts 16kHz mono PCM16 audio in arbitrary-sized frames (e.g. 480 samples for
a 30ms network frame) and internally re-chunks it into the 512-sample windows
the Silero model requires. Wraps `silero_vad.VADIterator` (the reference
streaming state machine, with threshold/hangover/padding already implemented
by the model authors) and turns its sample-offset events into `VadEvent`s that
carry the actual padded utterance audio, ready for the ASR stage.

The Silero ONNX weights ship inside the `silero-vad` pip package (no network
call at runtime), so this works fully offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

import numpy as np

from app.config import VadConfig

MODEL_WINDOW_SAMPLES = 512  # fixed window size the 16kHz Silero VAD model requires


class VadEventType(str, Enum):
    SPEECH_START = "speech_start"
    SPEECH_END = "speech_end"


@dataclass
class VadEvent:
    type: VadEventType
    sample_pos: int
    audio: np.ndarray | None = None  # int16 mono PCM, set only for SPEECH_END


class VadIteratorLike(Protocol):
    """Subset of silero_vad.VADIterator's interface, for injecting fakes in tests."""

    def __call__(self, x: np.ndarray, return_seconds: bool = False) -> dict[str, int] | None: ...
    def reset_states(self) -> None: ...


def _load_default_iterator(cfg: VadConfig, sample_rate: int) -> VadIteratorLike:
    from silero_vad import VADIterator, load_silero_vad

    model = load_silero_vad(onnx=True)
    return VADIterator(
        model,
        threshold=cfg.threshold,
        sampling_rate=sample_rate,
        min_silence_duration_ms=cfg.min_silence_ms,
        speech_pad_ms=cfg.speech_pad_ms,
    )


@dataclass
class SpeechEndpointer:
    """Stateful, single-stream speech endpointer. One instance per WS connection."""

    cfg: VadConfig
    sample_rate: int = 16000
    iterator: VadIteratorLike | None = None

    _pending: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int16), init=False)
    _history: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int16), init=False)
    _history_start_sample: int = field(default=0, init=False)
    _sample_pos: int = field(default=0, init=False)
    _triggered: bool = field(default=False, init=False)
    _speech_start_sample: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if self.iterator is None:
            self.iterator = _load_default_iterator(self.cfg, self.sample_rate)
        self._lookback_samples = self.cfg.speech_pad_ms * self.sample_rate // 1000 + MODEL_WINDOW_SAMPLES

    def push(self, pcm16: bytes) -> list[VadEvent]:
        """Feed one frame of 16kHz mono PCM16 audio. Returns zero or more events."""
        samples = np.frombuffer(pcm16, dtype=np.int16)
        self._pending = np.concatenate([self._pending, samples])
        self._history = np.concatenate([self._history, samples])

        events: list[VadEvent] = []
        while len(self._pending) >= MODEL_WINDOW_SAMPLES:
            window, self._pending = (
                self._pending[:MODEL_WINDOW_SAMPLES],
                self._pending[MODEL_WINDOW_SAMPLES:],
            )
            self._sample_pos += MODEL_WINDOW_SAMPLES
            events.extend(self._process_window(window))

        self._trim_history()
        return events

    def current_utterance_audio(self) -> np.ndarray | None:
        """Audio accumulated so far for an in-progress (not yet ended) utterance.

        Lets the pipeline run local-agreement partial ASR decodes *during*
        speech, without waiting for the VAD SPEECH_END event.
        """
        if not self._triggered:
            return None
        return self._slice_history(self._speech_start_sample, self._sample_pos)

    def flush(self) -> list[VadEvent]:
        """Force-close an open utterance (e.g. end of WAV file / stream close)."""
        if not self._triggered:
            return []
        audio = self._slice_history(self._speech_start_sample, self._sample_pos)
        self._triggered = False
        assert self.iterator is not None
        self.iterator.reset_states()
        return [self._maybe_speech_end(self._speech_start_sample, self._sample_pos, audio)]

    def _process_window(self, window: np.ndarray) -> list[VadEvent]:
        assert self.iterator is not None
        result = self.iterator(window)
        if result is None:
            return []
        if "start" in result:
            self._triggered = True
            self._speech_start_sample = result["start"]
            return [VadEvent(type=VadEventType.SPEECH_START, sample_pos=result["start"])]
        end_sample = result["end"]
        self._triggered = False
        audio = self._slice_history(self._speech_start_sample, end_sample)
        event = self._maybe_speech_end(self._speech_start_sample, end_sample, audio)
        return [event] if event else []

    def _maybe_speech_end(self, start_sample: int, end_sample: int, audio: np.ndarray) -> VadEvent | None:
        duration_ms = (end_sample - start_sample) * 1000 / self.sample_rate
        if duration_ms < self.cfg.min_speech_ms:
            return None  # too short - treat as a blip/click, not an utterance
        return VadEvent(type=VadEventType.SPEECH_END, sample_pos=end_sample, audio=audio)

    def _slice_history(self, start_sample: int, end_sample: int) -> np.ndarray:
        local_start = max(0, start_sample - self._history_start_sample)
        local_end = max(local_start, end_sample - self._history_start_sample)
        return self._history[local_start:local_end].copy()

    def _trim_history(self) -> None:
        """Bound memory: keep only enough back-history to satisfy speech_pad lookback."""
        if self._triggered:
            keep_from_sample = self._speech_start_sample
        else:
            keep_from_sample = self._sample_pos - self._lookback_samples
        local_keep_from = max(0, keep_from_sample - self._history_start_sample)
        if local_keep_from > 0:
            self._history = self._history[local_keep_from:]
            self._history_start_sample += local_keep_from
