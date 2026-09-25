"""How much of an utterance is *voiced* - the vocal cords vibrating at a pitch.

Speech is mostly voiced (every vowel); breaths, keyboard clicks, a chair
creaking and a call's comfort noise are not, but they can fool a speech
detector and then Whisper, which "hears" stock phrases in them ("شكراً",
"Thank you"). Pipeline drops utterances with too little voicing before they
reach ASR (vad.min_voiced_ms).

Per 30ms frame: pre-emphasis (so a low rumble can't pose as a pitch), then the
normalized autocorrelation over lags for 80-400 Hz. A frame is voiced when it
peaks strongly *inside* that range - low-pass noise also correlates at short
lags, but its correlation just falls off from the shortest lag instead of
peaking at a pitch period.
"""

from __future__ import annotations

import numpy as np

FRAME_MS = 30
PITCH_HZ = (80, 400)
MIN_CORRELATION = 0.45
MIN_FRAME_DBFS = -55.0  # quieter frames aren't examined at all


def voiced_ms(pcm16: np.ndarray, sample_rate: int = 16000) -> float:
    frame = int(sample_rate * FRAME_MS / 1000)
    lo, hi = sample_rate // PITCH_HZ[1], sample_rate // PITCH_HZ[0]
    min_energy = (10 ** (MIN_FRAME_DBFS / 20) * 32768) ** 2 * frame
    audio = pcm16.astype(np.float64)
    voiced_frames = 0
    for start in range(0, len(audio) - frame + 1, frame):
        x = audio[start : start + frame]
        if float(np.dot(x, x)) < min_energy:
            continue
        x = np.append(x[0], x[1:] - 0.97 * x[:-1])  # pre-emphasis
        x -= x.mean()
        r0 = float(np.dot(x, x))
        if r0 <= 0:
            continue
        r = np.array([np.dot(x[:-lag], x[lag:]) for lag in range(lo, hi + 1)]) / r0
        peak = int(np.argmax(r))
        if 0 < peak < len(r) - 1 and r[peak] >= MIN_CORRELATION:
            voiced_frames += 1
    return voiced_frames * FRAME_MS
