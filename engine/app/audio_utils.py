"""Small audio I/O helpers shared by the CLI harness, benchmark script and tests.

Normalizes arbitrary WAV files to the engine's wire format (16kHz mono PCM16)
and slices a buffer into fixed-size frames to simulate a live network stream.
"""

from __future__ import annotations

import audioop  # stdlib; adequate quality for offline resampling of test/CLI input
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import soundfile as sf

TARGET_SAMPLE_RATE = 16000


def load_wav_as_pcm16_mono(path: str | Path, target_sample_rate: int = TARGET_SAMPLE_RATE) -> np.ndarray:
    """Load a WAV file (any sample rate / channel count) as int16 mono PCM at target_sample_rate."""
    data, sr = sf.read(str(path), dtype="int16", always_2d=True)
    mono = data[:, 0] if data.shape[1] == 1 else data.mean(axis=1).astype(np.int16)
    if sr == target_sample_rate:
        return mono
    converted, _ = audioop.ratecv(mono.tobytes(), 2, 1, sr, target_sample_rate, None)
    return np.frombuffer(converted, dtype=np.int16)


def iter_frames(pcm16: np.ndarray, frame_samples: int) -> Iterator[np.ndarray]:
    """Yield fixed-size int16 frames, zero-padding the final partial frame."""
    total = len(pcm16)
    for offset in range(0, total, frame_samples):
        chunk = pcm16[offset : offset + frame_samples]
        if len(chunk) < frame_samples:
            chunk = np.pad(chunk, (0, frame_samples - len(chunk)))
        yield chunk


def pcm16_to_float32(pcm16: np.ndarray) -> np.ndarray:
    """Convert int16 samples to float32 in [-1, 1], the format faster-whisper expects."""
    return (pcm16.astype(np.float32) / 32768.0).clip(-1.0, 1.0)
