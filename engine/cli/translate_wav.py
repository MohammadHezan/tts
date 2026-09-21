"""CLI test harness: run the engine pipeline against a WAV file, streaming
captions to stdout as they're produced - the same event stream a WebSocket
client would receive.

Usage:
    python -m cli.translate_wav --file path/to/audio.wav
    python -m cli.translate_wav --file path/to/audio.wav --dry-run     # fake providers, no models/network
    python -m cli.translate_wav --file path/to/audio.wav --fast        # ignore real-time pacing
    python -m cli.translate_wav --file path/to/audio.wav --play-audio  # speak the translation out loud
    python -m cli.translate_wav --file path/to/audio.wav --save-audio-dir out/

By default frames are fed at real-time pace (matching a live mic), so the
printed latency_ms values are meaningful end-to-end numbers, not just decode
time. Run from the engine/ directory.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from app.audio_utils import iter_frames, load_wav_as_pcm16_mono
from app.config import EngineConfig, load_config
from app.logging_utils import configure_logging
from app.pipeline import Pipeline
from app.providers.base import build_asr_provider, build_translator_provider, build_tts_provider
from app.schema import EventType, PipelineEvent
from cli.formatting import format_event


def _apply_dry_run(cfg: EngineConfig) -> None:
    cfg.asr.provider = "fake"
    cfg.translator.provider = "fake"
    if cfg.tts.provider != "none":
        cfg.tts.provider = "fake"


async def run(args: argparse.Namespace) -> None:
    cfg = load_config()
    if args.dry_run:
        _apply_dry_run(cfg)
    if args.play_audio or args.save_audio_dir:
        if cfg.tts.provider == "none":
            cfg.tts.provider = "fake" if args.dry_run else "multi_voice"
    configure_logging(cfg.logging)

    asr = build_asr_provider(cfg.asr)
    translator = build_translator_provider(cfg.translator)
    tts = build_tts_provider(cfg.tts)
    pipeline = Pipeline(cfg, asr, translator, tts)

    player = None
    if args.play_audio:
        from cli.audio_playback import AudioPlayer

        player = AudioPlayer()

    save_dir = Path(args.save_audio_dir) if args.save_audio_dir else None
    if save_dir:
        save_dir.mkdir(parents=True, exist_ok=True)
    audio_count = 0

    pcm = load_wav_as_pcm16_mono(args.file, target_sample_rate=cfg.audio.sample_rate_hz)
    frame_samples = cfg.audio.frame_samples
    frame_duration_s = cfg.audio.frame_ms / 1000

    def emit(event: PipelineEvent) -> None:
        nonlocal audio_count
        print(format_event(event))
        if event.type is EventType.AUDIO and event.audio:
            if player is not None:
                player.enqueue(event.audio, event.audio_sample_rate or 16000)
            if save_dir is not None:
                audio_count += 1
                out_path = save_dir / f"{event.turn_id}_{audio_count:02d}.wav"
                sf.write(out_path, _pcm16_bytes_to_array(event.audio), event.audio_sample_rate or 16000)

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
    if player is not None:
        player.close()


def _pcm16_bytes_to_array(pcm16: bytes) -> np.ndarray:
    return np.frombuffer(pcm16, dtype=np.int16)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", required=True, help="Path to a WAV file (any sample rate/channel count)")
    parser.add_argument("--dry-run", action="store_true", help="Use fake ASR/translator/TTS providers (no models, no network)")
    parser.add_argument("--fast", action="store_true", help="Process as fast as possible instead of real-time pacing")
    parser.add_argument("--play-audio", action="store_true", help="Play the translated speech out loud (enables TTS if config.yaml has it off)")
    parser.add_argument("--save-audio-dir", help="Write each translated sentence's audio to a WAV file in this directory")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
