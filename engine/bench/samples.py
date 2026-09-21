"""Builds the benchmark sample set.

By default, synthesizes a small retail/furniture-domain sentence set with the
system `espeak-ng` so `python -m bench.benchmark` is runnable out of the box.
This is a *repeatable smoke benchmark* to validate the pipeline and get
latency numbers - espeak-ng's robotic voice is not representative of real
speech difficulty, so WER measured against it is optimistic. For a
trustworthy WER number, record your own audio + reference transcripts and
pass `--manifest your_manifest.json` to bench/benchmark.py.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"

_SENTENCES = [
    "Good afternoon, thank you for visiting our showroom today.",
    "We have a new collection of Italian leather sofas arriving next month.",
    "The oak dining table costs one thousand two hundred dollars.",
    "Can you confirm the shipment will arrive by Friday afternoon.",
    "Our marketing campaign will launch across Instagram and retail stores in March.",
]


@dataclass
class BenchmarkSample:
    wav_path: Path
    reference: str


def build_default_manifest() -> list[BenchmarkSample]:
    if shutil.which("espeak-ng") is None:
        raise RuntimeError(
            "espeak-ng not found on PATH. Install it (e.g. `apt install espeak-ng` on "
            "Debian/Ubuntu, `brew install espeak-ng` on macOS) to auto-generate the "
            "smoke benchmark set, or pass --manifest with your own recorded audio."
        )
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    samples = []
    for i, sentence in enumerate(_SENTENCES):
        wav_path = DATA_DIR / f"sample_{i:02d}.wav"
        if not wav_path.exists():
            subprocess.run(
                ["espeak-ng", "-v", "en-us", "-s", "160", "-w", str(wav_path), sentence],
                check=True,
                capture_output=True,
            )
        samples.append(BenchmarkSample(wav_path=wav_path, reference=sentence))
    return samples


def load_manifest(path: Path) -> list[BenchmarkSample]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    base = path.parent
    return [BenchmarkSample(wav_path=(base / item["wav"]).resolve(), reference=item["reference"]) for item in raw]
