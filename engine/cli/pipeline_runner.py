"""Shared "one full listen -> translate -> speak loop against two audio
devices" runner. One `Pipeline`, its own ASR/translator/TTS instances end to
end, reading from `input_device` and speaking to `output_device` until
cancelled - the same shape `cli/translate_mic.py` runs once for a live mic
session. Used by both `cli/translate_call.py` (two of these, against manual
device indices for a human's own mic/speakers plus a virtual cable) and
`cli/meeting_bot.py` (one of these, against a headless browser's virtual
devices) - extracted here instead of duplicated once a second caller needed
the exact same loop.
"""

from __future__ import annotations

import asyncio
import queue
import sys

import sounddevice as sd

from app.config import EngineConfig
from app.pipeline import Pipeline
from app.providers.base import build_asr_provider, build_translator_provider, build_tts_provider
from app.schema import EventType, PipelineEvent
from cli.audio_playback import AudioPlayer
from cli.formatting import format_event


async def run_direction(
    label: str,
    cfg: EngineConfig,
    input_device: int | str | None,
    output_device: int | str | None,
) -> None:
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
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            async for event in pipeline.flush():
                emit(event)
            player.close()
