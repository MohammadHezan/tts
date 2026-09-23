"""Two-way live interpreter for a Zoom/Google Meet call: runs two independent
translation pipelines concurrently, wired to different audio devices, so you
speak in your language and the other person hears the translation through
the call, and whatever they say comes back to you translated too.

This only works because Zoom/Meet on desktop let you choose *any* input/
output device for the call - there's no special integration with either app,
we're just another audio device they can select. You need one virtual audio
device installed first (a "virtual cable") - see README.md's "Call mode"
section for OS-specific setup (VB-CABLE on Windows; a PipeWire/PulseAudio
null-sink, no install needed, on Linux). This is NOT possible on Android -
the OS blocks apps from capturing other apps' call audio and there's no way
for a third-party app to act as another app's microphone; see README.md.

Once the virtual cable exists and Zoom/Meet's Microphone is set to it:

    OUTGOING: --mic-device (your real mic) -> translate -> --mic-out-device
              (the virtual cable's input - Zoom/Meet reads this as "your mic")
    INCOMING: --loopback-device (captures whatever Zoom/Meet is playing -
              a loopback/monitor device, not the virtual cable) -> translate
              -> --speaker-device (your real headphones/speakers)

Usage:
    python -m cli.translate_call --list-devices     # find every device index/name first
    python -m cli.translate_call \\
        --mic-device 1 --mic-out-device 5 \\
        --loopback-device 2 --speaker-device 4
    python -m cli.translate_call --dry-run ...       # fake providers, no models/network

Running two directions concurrently roughly doubles CPU/RAM use versus
cli/translate_mic.py (two full ASR+translator+TTS pipelines, one per
direction - AsrProvider is explicitly one-instance-per-pipeline, so this
can't share a single ASR model between directions). If your machine can't
keep up in real time, use a smaller asr.model in config.yaml (e.g. "small"
or "base" instead of "large-v3-turbo") for call mode.
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


async def _run_direction(
    label: str,
    cfg: EngineConfig,
    input_device: int | None,
    output_device: int | None,
) -> None:
    """One full listen -> translate -> speak pipeline, its own ASR/translator/TTS
    instances end to end, reading from `input_device` and speaking to
    `output_device`. Structurally identical to cli/translate_mic.py's run() -
    call mode is just two of these running side by side.
    """
    asr = build_asr_provider(cfg.asr)
    translator = build_translator_provider(cfg.translator)
    tts = build_tts_provider(cfg.tts)
    pipeline = Pipeline(cfg, asr, translator, tts)
    player = AudioPlayer(device=output_device)

    frame_samples = cfg.audio.frame_samples
    audio_q: queue.Queue[bytes] = queue.Queue()

    def on_audio(indata: object, frames: int, time_info: object, status: sd.CallbackFlags) -> None:
        if status:
            print(f"[{label}] {status}", file=sys.stderr)
        audio_q.put(bytes(indata))  # type: ignore[arg-type]

    def emit(event: PipelineEvent) -> None:
        print(f"[{label}] {format_event(event)}")
        if event.type is EventType.AUDIO and event.audio:
            player.enqueue(event.audio, event.audio_sample_rate or 16000)

    loop = asyncio.get_event_loop()
    with sd.RawInputStream(
        samplerate=cfg.audio.sample_rate_hz,
        blocksize=frame_samples,
        device=input_device,
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
            player.close()


async def run(args: argparse.Namespace) -> None:
    cfg = load_config()
    if args.dry_run:
        _apply_dry_run(cfg)
    if cfg.tts.provider == "none":
        cfg.tts.provider = "fake" if args.dry_run else "multi_voice"
    configure_logging(cfg.logging)

    print("Call mode - two live translation directions running concurrently:")
    print(f"  OUT (you -> them): mic device {args.mic_device} -> translated speech -> device {args.mic_out_device} (Zoom/Meet's mic)")
    print(f"  IN  (them -> you): device {args.loopback_device} (call audio) -> translated speech -> speaker device {args.speaker_device}")
    print("Press Ctrl+C to stop.")

    await asyncio.gather(
        _run_direction("OUT", cfg, args.mic_device, args.mic_out_device),
        _run_direction("IN", cfg, args.loopback_device, args.speaker_device),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Use fake ASR/translator/TTS providers (no models, no network)")
    parser.add_argument("--mic-device", type=int, default=None, help="Your real microphone")
    parser.add_argument("--mic-out-device", type=int, default=None, help="Virtual cable's input - set as Zoom/Meet's Microphone")
    parser.add_argument("--loopback-device", type=int, default=None, help="Loopback/monitor device capturing Zoom/Meet's own audio output")
    parser.add_argument("--speaker-device", type=int, default=None, help="Your real speakers/headphones")
    parser.add_argument("--list-devices", action="store_true", help="List audio devices and exit")
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
