"""CLI test harness: run the engine pipeline against a live microphone stream.

Usage:
    python -m cli.translate_mic
    python -m cli.translate_mic --dry-run       # fake providers, no models/network
    python -m cli.translate_mic --list-devices  # find your input device index
    python -m cli.translate_mic --device 3

Requires a working microphone and the system PortAudio library (see README's
"Hardware" section - Linux: `apt install libportaudio2`, macOS/Windows ship
it via the sounddevice wheel). Not runnable in this cloud sandbox (no audio
hardware); validated here via translate_wav.py plus the pytest integration
test instead - run this script on your own machine to confirm mic capture.
"""

from __future__ import annotations

import argparse
import asyncio
import queue
import sys

import sounddevice as sd

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

    frame_samples = cfg.audio.frame_samples
    audio_q: queue.Queue[bytes] = queue.Queue()

    def on_audio(indata: object, frames: int, time_info: object, status: sd.CallbackFlags) -> None:
        if status:
            print(f"[mic] {status}", file=sys.stderr)
        audio_q.put(bytes(indata))  # type: ignore[arg-type]

    def emit(event: PipelineEvent) -> None:
        print(format_event(event))

    loop = asyncio.get_event_loop()
    print("Listening on the microphone. Press Ctrl+C to stop.")
    with sd.RawInputStream(
        samplerate=cfg.audio.sample_rate_hz,
        blocksize=frame_samples,
        device=args.device,
        channels=cfg.audio.channels,
        dtype="int16",
        callback=on_audio,
    ):
        try:
            while True:
                frame = await loop.run_in_executor(None, audio_q.get)
                async for event in pipeline.process_frame(frame):
                    emit(event)
        except KeyboardInterrupt:
            pass
        finally:
            async for event in pipeline.flush():
                emit(event)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Use fake ASR/translator providers (no models, no network)")
    parser.add_argument("--device", type=int, default=None, help="Input device index (see --list-devices)")
    parser.add_argument("--list-devices", action="store_true", help="List audio input devices and exit")
    args = parser.parse_args()

    if args.list_devices:
        print(sd.query_devices())
        return

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
