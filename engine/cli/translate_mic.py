"""CLI test harness: live bidirectional interpreter over the default (or a
chosen) microphone and speaker/headset - the minimal "desktop app" for the
listen -> translate -> speak-it-back loop, ahead of the full Phase 5 desktop
overlay UI.

Speak English, hear Arabic; speak Arabic, hear English - direction is
automatic (ASR language auto-detection). Point --device / --output-device at
your Bluetooth-paired Galaxy Buds' input/output (however your OS exposes
them; `--list-devices` shows every device the OS knows about, Bluetooth ones
included) and this already gives you live bidirectional voice translation
through the earbuds from a laptop - no native app needed to test the core UX.

Usage:
    python -m cli.translate_mic
    python -m cli.translate_mic --dry-run                # fake providers, no models/network
    python -m cli.translate_mic --list-devices            # find your input/output device indices
    python -m cli.translate_mic --device 3 --output-device 4

Requires a working microphone and the system PortAudio library (see README's
"Hardware" section - Linux: `apt install libportaudio2`, macOS/Windows ship
it via the sounddevice wheel). Not runnable in this cloud sandbox (no audio
hardware); validated here via translate_wav.py plus the pytest integration
test instead - run this script on your own machine to confirm mic capture
and playback.
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
from app.providers.base import build_asr_provider, build_translator_provider, build_tts_provider
from app.schema import EventType, PipelineEvent
from cli.audio_playback import AudioPlayer
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
    if not args.no_speak and cfg.tts.provider == "none":
        cfg.tts.provider = "fake" if args.dry_run else "multi_voice"
    configure_logging(cfg.logging)

    asr = build_asr_provider(cfg.asr)
    translator = build_translator_provider(cfg.translator)
    tts = build_tts_provider(cfg.tts) if not args.no_speak else None
    pipeline = Pipeline(cfg, asr, translator, tts)

    player = AudioPlayer(device=args.output_device) if tts is not None else None

    frame_samples = cfg.audio.frame_samples
    audio_q: queue.Queue[bytes] = queue.Queue()

    def on_audio(indata: object, frames: int, time_info: object, status: sd.CallbackFlags) -> None:
        if status:
            print(f"[mic] {status}", file=sys.stderr)
        audio_q.put(bytes(indata))  # type: ignore[arg-type]

    def emit(event: PipelineEvent) -> None:
        print(format_event(event))
        if player is not None and event.type is EventType.AUDIO and event.audio:
            player.enqueue(event.audio, event.audio_sample_rate or 16000)

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
            if player is not None:
                player.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Use fake ASR/translator/TTS providers (no models, no network)")
    parser.add_argument("--device", type=int, default=None, help="Input device index (see --list-devices)")
    parser.add_argument("--output-device", type=int, default=None, help="Output device index for spoken translations (see --list-devices)")
    parser.add_argument("--no-speak", action="store_true", help="Captions only - skip TTS/playback even if config.yaml enables it")
    parser.add_argument("--list-devices", action="store_true", help="List audio input/output devices and exit")
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
