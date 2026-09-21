"""CLI test harness: run the engine pipeline against a WAV file, streaming
captions to stdout as they're produced - the same event stream a WebSocket
client would receive.

Usage:
    python -m cli.translate_wav --file path/to/audio.wav
    python -m cli.translate_wav --file path/to/audio.wav --dry-run   # fake providers, no models/network
    python -m cli.translate_wav --file path/to/audio.wav --fast      # ignore real-time pacing

By default frames are fed at real-time pace (matching a live mic), so the
printed latency_ms values are meaningful end-to-end numbers, not just decode
time. Run from the engine/ directory.
"""

from __future__ import annotations

import argparse
import asyncio
import time

from app.audio_utils import iter_frames, load_wav_as_pcm16_mono
from app.config import EngineConfig, load_config
from app.logging_utils import configure_logging
from app.pipeline import Pipeline
from app.providers.base import build_asr_provider, build_translator_provider
from app.schema import PipelineEvent
from cli.formatting import format_event


def _apply_dry_run(cfg: EngineConfig) -> None:
    cfg.asr.provider = "fake"
    cfg.translator.provider = "fake"


async def run(args: argparse.Namespace) -> None:
    cfg = load_config()
    if args.dry_run:
        _apply_dry_run(cfg)
    configure_logging(cfg.logging)

    asr = build_asr_provider(cfg.asr)
    translator = build_translator_provider(cfg.translator)
    pipeline = Pipeline(cfg, asr, translator)

    pcm = load_wav_as_pcm16_mono(args.file, target_sample_rate=cfg.audio.sample_rate_hz)
    frame_samples = cfg.audio.frame_samples
    frame_duration_s = cfg.audio.frame_ms / 1000

    def emit(event: PipelineEvent) -> None:
        print(format_event(event))

    t0 = time.perf_counter()
    for frame in iter_frames(pcm, frame_samples):
        frame_start = time.perf_counter()
        async for event in pipeline.process_frame(frame.tobytes()):
            emit(event)
        if not args.fast:
            elapsed = time.perf_counter() - frame_start
            await asyncio.sleep(max(0.0, frame_duration_s - elapsed))
    async for event in pipeline.flush():
        emit(event)

    print(f"--- done in {time.perf_counter() - t0:.2f}s wall clock ---")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", required=True, help="Path to a WAV file (any sample rate/channel count)")
    parser.add_argument("--dry-run", action="store_true", help="Use fake ASR/translator providers (no models, no network)")
    parser.add_argument("--fast", action="store_true", help="Process as fast as possible instead of real-time pacing")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
