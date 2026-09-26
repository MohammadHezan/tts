"""Streaming speech endpointing on top of Silero VAD.

Accepts 16kHz mono PCM16 audio in arbitrary-sized frames (e.g. 480 samples for
a 30ms network frame) and internally re-chunks it into the 512-sample windows
the Silero model requires. Wraps `silero_vad.VADIterator` (the reference
streaming state machine, with threshold/hangover/padding already implemented
by the model authors) and turns its sample-offset events into `VadEvent`s that
carry the actual padded utterance audio, ready for the ASR stage.

The Silero ONNX weights ship inside the `silero-vad` pip package (no network
call at runtime), so this works fully offline.

With vad.phrase_min_ms set, long speech is also handed on phrase by phrase
(SPEECH_END for the phrase, SPEECH_START for the rest, at the same sample)
while the speaker carries on - see _maybe_end_phrase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

import numpy as np

from app.config import VadConfig

MODEL_WINDOW_SAMPLES = 512  # fixed window size the 16kHz Silero VAD model requires
PAUSE_FRAME_MS = 20  # loudness resolution for spotting a pause mid-phrase
PAUSE_BELOW_SPEECH_DB = 18  # a pause is at least this much quieter than the phrase's speech
QUIETEST_FRAME_MS = 100  # a phrase cut without a pause goes in the quietest stretch this long
QUIETEST_HOP_MS = 10


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

    def _samples(self, ms: int) -> int:
        return ms * self.sample_rate // 1000

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
            return self._maybe_end_phrase()
        if "start" in result:
            self._triggered = True
            self._speech_start_sample = result["start"]
            return [VadEvent(type=VadEventType.SPEECH_START, sample_pos=result["start"])]
        end_sample = result["end"]
        self._triggered = False
        audio = self._slice_history(self._speech_start_sample, end_sample)
        event = self._maybe_speech_end(self._speech_start_sample, end_sample, audio)
        return [event] if event else []

    def _maybe_end_phrase(self) -> list[VadEvent]:
        """Mid-utterance: end the phrase so far if it is long enough and the
        speaker has paused briefly, or if it has run too long without one."""
        cfg = self.cfg
        if not self._triggered or cfg.phrase_min_ms is None:
            return []
        start = self._speech_start_sample
        length = self._sample_pos - start
        if length < self._samples(cfg.phrase_min_ms):
            return []
        if self._paused(start):
            split = self._sample_pos - self._samples(cfg.phrase_pause_ms) // 2  # the middle of the pause
        elif cfg.phrase_max_ms is not None and length >= self._samples(cfg.phrase_max_ms):
            split = self._quietest_point(start + self._samples(cfg.phrase_min_ms))
        else:
            return []
        audio = self._slice_history(start, split)
        # The rest of the utterance carries on from the split. If the speaker
        # doesn't, it's only the padding up to VADIterator's own end - shorter
        # than min_speech_ms, so it never becomes an utterance of its own.
        self._speech_start_sample = split
        return [
            VadEvent(type=VadEventType.SPEECH_END, sample_pos=split, audio=audio),
            VadEvent(type=VadEventType.SPEECH_START, sample_pos=split),
        ]

    def _paused(self, start: int) -> bool:
        """The speaker has been quiet for the last phrase_pause_ms: every
        PAUSE_FRAME_MS of it well below the phrase's speech. Judged by loudness,
        not by Silero: its probability also dips under the threshold inside
        words, and a phrase cut there splits a word in half ("في بـ|داية")."""
        pause = self._samples(self.cfg.phrase_pause_ms)
        frame = self._samples(PAUSE_FRAME_MS)
        audio = self._slice_history(start, self._sample_pos).astype(np.float64)
        tail = max(1, pause // frame)
        if len(audio) < (tail + 1) * frame:
            return False
        frames = audio[len(audio) % frame :].reshape(-1, frame)
        db = 10 * np.log10(np.mean(frames**2, axis=1) + 1.0)
        speech = float(np.percentile(db[:-tail], 90))
        return bool(np.all(db[-tail:] < speech - PAUSE_BELOW_SPEECH_DB))

    def _quietest_point(self, from_sample: int) -> int:
        """Middle of the quietest QUIETEST_FRAME_MS stretch from from_sample to
        now - most likely a gap between two words. Long enough that the brief
        silence inside a consonant (a "d", a "t") doesn't count."""
        frame = self._samples(QUIETEST_FRAME_MS)
        hop = self._samples(QUIETEST_HOP_MS)
        audio = self._slice_history(from_sample, self._sample_pos).astype(np.float64)
        if len(audio) < frame:
            return self._sample_pos
        energy = np.convolve(audio**2, np.ones(frame), mode="valid")[::hop]
        return from_sample + int(np.argmin(energy)) * hop + frame // 2

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
