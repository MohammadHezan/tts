"""Unit tests for app/vad.py's SpeechEndpointer.

Most tests use a scripted fake iterator (not the real Silero model) so they
are fast and fully deterministic. One test exercises the real bundled Silero
ONNX model end-to-end against synthesized audio, to catch integration bugs
the scripted tests can't (wrong argument order, API drift, etc.).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np

from app.config import VadConfig
from app.vad import MODEL_WINDOW_SAMPLES, SpeechEndpointer, VadEventType


class ScriptedIterator:
    """Deterministic stand-in for silero_vad.VADIterator.

    `schedule` maps a 0-based window-call index to the dict VADIterator would
    have returned for that window (e.g. {"start": sample_idx} / {"end": sample_idx}).
    """

    def __init__(self, schedule: dict[int, dict[str, int]]) -> None:
        self._schedule = schedule
        self.call_count = 0
        self.reset_calls = 0

    def __call__(self, x: np.ndarray, return_seconds: bool = False) -> dict[str, int] | None:
        result = self._schedule.get(self.call_count)
        self.call_count += 1
        return result

    def reset_states(self) -> None:
        self.reset_calls += 1


def _silence_frame(n_samples: int = MODEL_WINDOW_SAMPLES) -> bytes:
    return np.zeros(n_samples, dtype=np.int16).tobytes()


def _make_endpointer(
    schedule: dict[int, dict[str, int]], **cfg_overrides: object
) -> tuple[SpeechEndpointer, ScriptedIterator]:
    cfg = VadConfig(**cfg_overrides)  # type: ignore[arg-type]
    iterator = ScriptedIterator(schedule)
    ep = SpeechEndpointer(cfg=cfg, sample_rate=16000, iterator=iterator)
    return ep, iterator


def test_speech_start_then_end_emits_correctly_sliced_audio() -> None:
    ep, _ = _make_endpointer({0: {"start": 0}, 3: {"end": 3 * MODEL_WINDOW_SAMPLES}}, min_speech_ms=0)

    events = []
    for _ in range(4):
        events.extend(ep.push(_silence_frame()))

    assert [e.type for e in events] == [VadEventType.SPEECH_START, VadEventType.SPEECH_END]
    start_event, end_event = events
    assert start_event.audio is None
    assert end_event.audio is not None
    assert len(end_event.audio) == 3 * MODEL_WINDOW_SAMPLES


def test_short_utterance_below_min_speech_ms_is_dropped() -> None:
    # 1 window = 512 samples = 32ms @16kHz, well under the 150ms default floor.
    ep, _ = _make_endpointer({0: {"start": 0}, 1: {"end": MODEL_WINDOW_SAMPLES}}, min_speech_ms=150)

    events = []
    for _ in range(2):
        events.extend(ep.push(_silence_frame()))

    assert [e.type for e in events] == [VadEventType.SPEECH_START]  # SPEECH_END was filtered out


def test_flush_force_closes_an_open_utterance() -> None:
    ep, iterator = _make_endpointer({0: {"start": 0}}, min_speech_ms=0)

    for _ in range(4):
        ep.push(_silence_frame())
    assert ep.current_utterance_audio() is not None

    flush_events = ep.flush()
    assert len(flush_events) == 1
    assert flush_events[0].type is VadEventType.SPEECH_END
    assert flush_events[0].audio is not None
    assert len(flush_events[0].audio) == 4 * MODEL_WINDOW_SAMPLES
    assert iterator.reset_calls == 1
    assert ep.current_utterance_audio() is None


def test_current_utterance_audio_is_none_when_idle() -> None:
    ep, _ = _make_endpointer({})  # never triggers
    for _ in range(3):
        ep.push(_silence_frame())
    assert ep.current_utterance_audio() is None


def test_history_stays_bounded_while_idle() -> None:
    ep, _ = _make_endpointer({})  # never triggers
    for _ in range(500):
        ep.push(_silence_frame())
    assert len(ep._history) <= ep._lookback_samples + MODEL_WINDOW_SAMPLES  # noqa: SLF001


def test_push_handles_frame_sizes_smaller_than_the_model_window() -> None:
    # Simulates real network frames (30ms = 480 samples) that don't divide
    # evenly into the model's fixed 512-sample window.
    ep, _ = _make_endpointer({0: {"start": 0}, 4: {"end": 4 * MODEL_WINDOW_SAMPLES}}, min_speech_ms=0)
    frame = np.zeros(480, dtype=np.int16).tobytes()

    events = []
    for _ in range(30):
        events.extend(ep.push(frame))

    types = {e.type for e in events}
    assert types == {VadEventType.SPEECH_START, VadEventType.SPEECH_END}


def test_real_silero_model_detects_speech_in_synthesized_audio(synthesized_wav: Callable[[str], Path]) -> None:
    from app.audio_utils import iter_frames, load_wav_as_pcm16_mono

    wav_path = synthesized_wav("Good afternoon, thank you for visiting our showroom today.")
    pcm = load_wav_as_pcm16_mono(wav_path)

    ep = SpeechEndpointer(cfg=VadConfig(), sample_rate=16000)  # real bundled Silero ONNX model

    events = []
    for frame in iter_frames(pcm, 480):
        events.extend(ep.push(frame.tobytes()))
    events.extend(ep.flush())

    assert [e.type for e in events] == [VadEventType.SPEECH_START, VadEventType.SPEECH_END]
    assert events[1].audio is not None
    assert len(events[1].audio) > 16000  # at least ~1s of speech captured
