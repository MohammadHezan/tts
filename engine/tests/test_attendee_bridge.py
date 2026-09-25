"""Attendee realtime-audio bridge: protocol in, translated speech out.

Plays Attendee's side of the websocket with a stand-in socket that streams
real (espeak-ng) speech as `realtime_audio.mixed` messages, through the real
Silero VAD and the fake ASR/translator/TTS, and checks what comes back is
valid `realtime_audio.bot_output` audio plus caption events for the dashboard.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi import WebSocketDisconnect

from app.attendee_bridge import ATTENDEE_SAMPLE_RATE, BotEventHub, bot_output_messages, run_bridge
from app.attendee_client import AttendeeClient, AttendeeError, AttendeeSettings
from app.audio_utils import load_wav_as_pcm16_mono
from app.config import AsrConfig, EngineConfig, TranslatorConfig
from app.pipeline import Pipeline
from app.providers.asr_fake import FakeAsr
from app.providers.translator_fake import FakeTranslator
from app.providers.tts_fake import FakeTts
from app.schema import EventType

BOT_ID = "bot_test123"
SPOKEN = "Welcome to our showroom, this sofa is handmade in Italy."


class FakeAttendeeSocket:
    """Duck-types the two WebSocket methods run_bridge uses."""

    def __init__(self, incoming: list[str]) -> None:
        self._incoming = list(incoming)
        self.sent: list[str] = []

    async def receive_text(self) -> str:
        if not self._incoming:
            raise WebSocketDisconnect(code=1000)
        return self._incoming.pop(0)

    async def send_text(self, message: str) -> None:
        self.sent.append(message)


def _mixed_audio_messages(pcm16: np.ndarray, chunk_ms: int = 20) -> list[str]:
    samples_per_chunk = ATTENDEE_SAMPLE_RATE * chunk_ms // 1000
    messages = []
    for i, offset in enumerate(range(0, len(pcm16), samples_per_chunk)):
        chunk = pcm16[offset : offset + samples_per_chunk].tobytes()
        messages.append(
            json.dumps(
                {
                    "bot_id": BOT_ID,
                    "trigger": "realtime_audio.mixed",
                    "data": {
                        "chunk": base64.b64encode(chunk).decode("ascii"),
                        "sample_rate": ATTENDEE_SAMPLE_RATE,
                        "timestamp_ms": i * chunk_ms,
                    },
                }
            )
        )
    return messages


async def test_bridge_turns_meeting_speech_into_bot_output_audio(
    synthesized_wav: Callable[[str], Path],
) -> None:
    cfg = EngineConfig(asr=AsrConfig(provider="fake"), translator=TranslatorConfig(provider="fake"))
    speech = load_wav_as_pcm16_mono(synthesized_wav(SPOKEN), target_sample_rate=ATTENDEE_SAMPLE_RATE)
    trailing_silence = np.zeros(int(ATTENDEE_SAMPLE_RATE * 1.5), dtype=np.int16)  # lets VAD end the utterance
    socket = FakeAttendeeSocket(_mixed_audio_messages(np.concatenate([speech, trailing_silence])))

    hub = BotEventHub()
    captions = hub.subscribe(BOT_ID)

    await run_bridge(
        socket,  # type: ignore[arg-type]
        lambda **hooks: Pipeline(cfg, FakeAsr(final_text=SPOKEN), FakeTranslator(), FakeTts(), **hooks),
        cfg.audio.sample_rate_hz,
        cfg.audio.frame_samples * 2,
        hub,
    )

    outputs = [json.loads(m) for m in socket.sent]
    assert outputs, "bridge never sent the bot's voice back to Attendee"
    assert {m["trigger"] for m in outputs} == {"realtime_audio.bot_output"}
    assert {m["data"]["sample_rate"] for m in outputs} == {ATTENDEE_SAMPLE_RATE}
    audio = b"".join(base64.b64decode(m["data"]["chunk"]) for m in outputs)
    assert len(audio) > ATTENDEE_SAMPLE_RATE // 2 * 2  # more than 0.5s of speech came back
    assert np.abs(np.frombuffer(audio, dtype=np.int16)).max() > 0

    published = []
    while not captions.empty():
        published.append(json.loads(captions.get_nowait()))
    types = [event["type"] for event in published]
    assert EventType.FINAL.value in types
    assert EventType.TRANSLATION.value in types
    assert EventType.AUDIO.value not in types  # audio goes to the meeting, not the caption feed
    assert EventType.PARTIAL.value not in types  # never rendered, would only churn the replay history
    final = next(event for event in published if event["type"] == EventType.FINAL.value)
    assert final["text"] == SPOKEN


def test_hub_replays_history_to_late_subscribers() -> None:
    # A real bot can finish a whole turn before the dashboard's socket connects
    # (or the page gets refreshed mid-meeting) - found by verify_meeting_bot.py.
    hub = BotEventHub(history=2)
    hub.publish(BOT_ID, "one")
    hub.publish(BOT_ID, "two")
    hub.publish(BOT_ID, "three")
    late = hub.subscribe(BOT_ID)
    hub.publish(BOT_ID, "four")
    assert [late.get_nowait() for _ in range(3)] == ["two", "three", "four"]
    assert hub.subscribe("some_other_bot").empty()


async def test_bridge_ignores_non_audio_triggers() -> None:
    cfg = EngineConfig(asr=AsrConfig(provider="fake"), translator=TranslatorConfig(provider="fake"))
    socket = FakeAttendeeSocket([json.dumps({"bot_id": BOT_ID, "trigger": "some.other_event", "data": {}})])
    await run_bridge(
        socket,  # type: ignore[arg-type]
        lambda **hooks: Pipeline(cfg, FakeAsr(), FakeTranslator(), FakeTts(), **hooks),
        cfg.audio.sample_rate_hz,
        cfg.audio.frame_samples * 2,
        BotEventHub(),
    )
    assert socket.sent == []


@pytest.mark.parametrize("tts_rate", [16000, 22050, 24000])  # Fake / Piper Arabic / Kokoro English
def test_bot_output_is_resampled_and_chunked_for_attendee(tts_rate: int) -> None:
    seconds = 1.0
    pcm16 = (np.sin(np.linspace(0, 2 * np.pi * 440 * seconds, int(tts_rate * seconds))) * 8000).astype(np.int16)

    messages = [json.loads(m) for m in bot_output_messages(pcm16.tobytes(), tts_rate)]

    assert len(messages) == 10  # 1s in 100ms chunks
    total = b"".join(base64.b64decode(m["data"]["chunk"]) for m in messages)
    assert abs(len(total) // 2 - ATTENDEE_SAMPLE_RATE) <= 2  # ~1s at 16kHz regardless of source rate


async def test_attendee_client_sends_realtime_audio_settings() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": BOT_ID, "state": "joining"})

    client = AttendeeClient(AttendeeSettings("http://attendee.test", "key123"), transport=httpx.MockTransport(handler))
    bot = await client.create_bot("https://meet.google.com/abc-defg-hij", "AI Interpreter", "ws://x/attendee/ws?token=t", 16000)
    await client.aclose()

    assert bot == {"id": BOT_ID, "state": "joining"}
    assert seen["method"] == "POST"
    assert seen["path"] == "/api/v1/bots"
    assert seen["auth"] == "Token key123"
    assert seen["body"] == {
        "meeting_url": "https://meet.google.com/abc-defg-hij",
        "bot_name": "AI Interpreter",
        "websocket_settings": {"audio": {"url": "ws://x/attendee/ws?token=t", "sample_rate": 16000}},
        "recording_settings": {"format": "none"},
    }


async def test_attendee_client_surfaces_attendee_errors() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(400, text='{"meeting_url":["invalid"]}'))
    client = AttendeeClient(AttendeeSettings("http://attendee.test", "key123"), transport=transport)
    with pytest.raises(AttendeeError) as caught:
        await client.create_bot("not-a-url", "Bot", "ws://x", 16000)
    await client.aclose()
    assert caught.value.status_code == 400
    assert "invalid" in caught.value.detail
