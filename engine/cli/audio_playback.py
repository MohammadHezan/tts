"""Sequential audio playback for the CLI harnesses' TTS output.

Plays queued PCM16 chunks back-to-back on a background thread so translated
sentences never overlap or clip, and so playback never blocks the mic-capture
/ frame-processing loop.
"""

from __future__ import annotations

import queue
import threading

import numpy as np
import sounddevice as sd


class AudioPlayer:
    def __init__(self, device: int | None = None) -> None:
        self._device = device
        self._queue: queue.Queue[tuple[np.ndarray, int] | None] = queue.Queue()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def enqueue(self, pcm16: bytes, sample_rate: int) -> None:
        if not pcm16:
            return
        audio = np.frombuffer(pcm16, dtype=np.int16)
        self._queue.put((audio, sample_rate))

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                break
            audio, sample_rate = item
            sd.play(audio, samplerate=sample_rate, device=self._device, blocking=True)

    def close(self) -> None:
        self._queue.put(None)
        self._thread.join(timeout=2)
