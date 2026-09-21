"""Shared pytest fixtures."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

ESPEAK_AVAILABLE = shutil.which("espeak-ng") is not None


@pytest.fixture(scope="session")
def synthesized_wav(tmp_path_factory: pytest.TempPathFactory) -> Callable[[str], Path]:
    """Factory fixture: synthesizes a sentence to WAV via espeak-ng.

    Used by tests that need real (if robotic) speech audio to exercise the
    real Silero VAD model end-to-end. Skips gracefully if espeak-ng isn't
    installed rather than failing the whole suite.
    """
    if not ESPEAK_AVAILABLE:
        pytest.skip("espeak-ng not installed - install it to run audio-based tests")

    counter = {"n": 0}

    def _make(text: str) -> Path:
        counter["n"] += 1
        out_dir = tmp_path_factory.mktemp("audio")
        wav_path = out_dir / f"speech_{counter['n']}.wav"
        subprocess.run(
            ["espeak-ng", "-v", "en-us", "-s", "160", "-w", str(wav_path), text],
            check=True,
            capture_output=True,
        )
        return wav_path

    return _make
