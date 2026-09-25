"""What keeps a meeting bot from talking when nobody spoke: Whisper's stock
phrases, too-quiet "utterances", its own voice coming back through someone's
speaker - and the mute switch."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Callable
from pathlib import Path

import numpy as np
from fastapi import WebSocketDisconnect

from app.attendee_bridge import ATTENDEE_SAMPLE_RATE, ECHO_WINDOW_S, BotControls, BotEventHub, EchoGuard, run_bridge
from app.audio_utils import load_wav_as_pcm16_mono
from app.config import AsrConfig, EngineConfig, TranslatorConfig, VadConfig
from app.pipeline import Pipeline, rms_dbfs
from app.providers.asr_fake import FakeAsr
from app.providers.asr_faster_whisper import is_known_hallucination, looks_like_non_speech
from app.providers.translator_fake import FakeTranslator
from app.providers.tts_fake import FakeTts
from app.schema import EventType
from app.vad import VadEvent, VadEventType

BOT_ID = "bot_guard"
SPOKEN = "Welcome to our showroom, this sofa is handmade in Italy."


def test_whisper_stock_phrases_are_dropped_only_on_their_own() -> None:
    for phantom in ["شكراً", "شكراً.", "شُكْرًا لَكُمْ", "ترجمة نانسي قنقر", "Thank you.", "thanks for watching!"]:
        assert is_known_hallucination(phantom), phantom
    for real in ["شكراً على الطلبية", "Thank you for the order.", "Okay.", "نعم", "Good morning"]:
        assert not is_known_hallucination(real), real


def test_whispers_own_non_speech_signals() -> None:
    assert looks_like_non_speech(no_speech_prob=0.7, avg_logprob=-0.2, compression_ratio=1.1)
    assert looks_like_non_speech(no_speech_prob=0.35, avg_logprob=-0.8, compression_ratio=1.1)
    assert looks_like_non_speech(no_speech_prob=0.05, avg_logprob=-1.2, compression_ratio=1.1)
    assert looks_like_non_speech(no_speech_prob=0.05, avg_logprob=-0.2, compression_ratio=3.0)
    assert not looks_like_non_speech(no_speech_prob=0.1, avg_logprob=-0.3, compression_ratio=1.2)


def test_echo_guard_recognizes_the_bots_own_words_for_a_while() -> None:
    now = [100.0]
    guard = EchoGuard(clock=lambda: now[0])
    guard.said("Good morning, thank you for joining the call.", "en")
    assert guard.is_echo("good morning thank you for joining the call", "en")
    assert guard.is_echo("Good morning, thanks for joining the call", "en")  # heard slightly differently
    assert not guard.is_echo("Good morning, thank you for joining the call.", "ar")  # other language
    assert not guard.is_echo("We would like twenty dining chairs.", "en")
    now[0] += ECHO_WINDOW_S + 1
    assert not guard.is_echo("Good morning, thank you for joining the call.", "en")


async def _run_turn(pipeline: Pipeline, audio: np.ndarray) -> list:
    events = []
    async for event in pipeline._handle_vad_event(VadEvent(VadEventType.SPEECH_START, 0)):
        events.append(event)
    async for event in pipeline._handle_vad_event(VadEvent(VadEventType.SPEECH_END, len(audio), audio=audio)):
        events.append(event)
    return events


async def test_too_quiet_utterances_never_reach_asr() -> None:
    cfg = EngineConfig(
        vad=VadConfig(min_utterance_dbfs=-50.0),
        asr=AsrConfig(provider="fake"),
        translator=TranslatorConfig(provider="fake"),
    )
    pipeline = Pipeline(cfg, FakeAsr(), FakeTranslator(), FakeTts())
    rng = np.random.default_rng(0)
    comfort_noise = (rng.standard_normal(16000) * 30).astype(np.int16)  # about -61 dBFS
    speech_level = (rng.standard_normal(16000) * 3000).astype(np.int16)  # about -21 dBFS
    assert rms_dbfs(comfort_noise) < -50 < rms_dbfs(speech_level)

    assert await _run_turn(pipeline, comfort_noise) == []
    types = [e.type for e in await _run_turn(pipeline, speech_level)]
    assert EventType.FINAL in types and EventType.TRANSLATION in types


async def test_rejected_transcripts_and_muted_speech() -> None:
    cfg = EngineConfig(asr=AsrConfig(provider="fake"), translator=TranslatorConfig(provider="fake"))
    loud = (np.random.default_rng(1).standard_normal(16000) * 3000).astype(np.int16)

    rejecting = Pipeline(cfg, FakeAsr(), FakeTranslator(), FakeTts(), accept_transcript=lambda text, lang: False)
    assert await _run_turn(rejecting, loud) == []

    silent = Pipeline(cfg, FakeAsr(), FakeTranslator(), FakeTts(), should_speak=lambda: False)
    types = [e.type for e in await _run_turn(silent, loud)]
    assert EventType.TRANSLATION in types  # captions keep going
    assert EventType.AUDIO not in types  # but nothing is synthesized


def _mixed(pcm16: np.ndarray) -> list[str]:
    step = ATTENDEE_SAMPLE_RATE // 50  # 20ms, a live meeting's cadence
    return [
        json.dumps(
            {
                "bot_id": BOT_ID,
                "trigger": "realtime_audio.mixed",
                "data": {"chunk": base64.b64encode(pcm16[i : i + step].tobytes()).decode(), "sample_rate": ATTENDEE_SAMPLE_RATE},
            }
        )
        for i in range(0, len(pcm16), step)
    ]


class ScriptedSocket:
    """First batch of meeting audio; then, once the bot has started talking, a second."""

    def __init__(self, first: list[str], while_bot_speaks: list[str]) -> None:
        self._first, self._second = list(first), list(while_bot_speaks)
        self.sent: list[str] = []

    async def receive_text(self) -> str:
        if self._first:
            return self._first.pop(0)
        if self._second:
            for _ in range(500):
                if self.sent:
                    break
                await asyncio.sleep(0.01)
            return self._second.pop(0)
        await asyncio.sleep(3)  # let the bot finish, then hang up
        raise WebSocketDisconnect(code=1000)

    async def send_text(self, message: str) -> None:
        self.sent.append(message)


def _finals(hub_queue: asyncio.Queue[str]) -> list[dict]:
    events = []
    while not hub_queue.empty():
        events.append(json.loads(hub_queue.get_nowait()))
    return [e for e in events if e["type"] == EventType.FINAL.value]


async def test_meeting_audio_is_ignored_while_the_bot_speaks(synthesized_wav: Callable[[str], Path]) -> None:
    cfg = EngineConfig(asr=AsrConfig(provider="fake"), translator=TranslatorConfig(provider="fake"))
    speech = load_wav_as_pcm16_mono(synthesized_wav(SPOKEN), target_sample_rate=ATTENDEE_SAMPLE_RATE)
    silence = np.zeros(int(ATTENDEE_SAMPLE_RATE * 1.5), dtype=np.int16)
    # The same sentence again while the bot talks - e.g. its own voice picked up
    # by a phone in the same room. It must not become a second turn.
    socket = ScriptedSocket(_mixed(np.concatenate([speech, silence])), _mixed(np.concatenate([speech, silence])))
    hub = BotEventHub()
    captions = hub.subscribe(BOT_ID)

    await run_bridge(
        socket,  # type: ignore[arg-type]
        lambda **hooks: Pipeline(cfg, FakeAsr(final_text=SPOKEN), FakeTranslator(), FakeTts(), **hooks),
        cfg.audio.sample_rate_hz,
        cfg.audio.frame_samples * 2,
        hub,
    )
    assert socket.sent, "the bot never spoke"
    assert len(_finals(captions)) == 1


async def test_muted_bot_keeps_captioning_but_stays_silent(synthesized_wav: Callable[[str], Path]) -> None:
    cfg = EngineConfig(asr=AsrConfig(provider="fake"), translator=TranslatorConfig(provider="fake"))
    speech = load_wav_as_pcm16_mono(synthesized_wav(SPOKEN), target_sample_rate=ATTENDEE_SAMPLE_RATE)
    silence = np.zeros(int(ATTENDEE_SAMPLE_RATE * 1.5), dtype=np.int16)
    controls = BotControls()
    controls.set_muted(BOT_ID, True)
    socket = ScriptedSocket(_mixed(np.concatenate([speech, silence])), [])
    hub = BotEventHub()
    captions = hub.subscribe(BOT_ID)

    await run_bridge(
        socket,  # type: ignore[arg-type]
        lambda **hooks: Pipeline(cfg, FakeAsr(final_text=SPOKEN), FakeTranslator(), FakeTts(), **hooks),
        cfg.audio.sample_rate_hz,
        cfg.audio.frame_samples * 2,
        hub,
        controls,
    )
    assert socket.sent == []
    assert len(_finals(captions)) == 1


def test_voicing_separates_speech_from_breaths_and_clicks(synthesized_wav: Callable[[str], Path]) -> None:
    from app.voicing import voiced_ms
    from scripts.simulate_meeting import room_noise

    speech = load_wav_as_pcm16_mono(synthesized_wav(SPOKEN), target_sample_rate=ATTENDEE_SAMPLE_RATE)
    short_answer = load_wav_as_pcm16_mono(synthesized_wav("yes"), target_sample_rate=ATTENDEE_SAMPLE_RATE)
    assert voiced_ms(speech) > 500
    assert voiced_ms(short_answer) >= 45  # the shipped min_voiced_ms keeps a one-word answer
    assert voiced_ms(room_noise(20)) == 0  # hum, keyboard clicks, breaths


async def test_unvoiced_utterances_never_reach_asr() -> None:
    from scripts.simulate_meeting import room_noise

    cfg = EngineConfig(
        vad=VadConfig(min_voiced_ms=45),
        asr=AsrConfig(provider="fake"),
        translator=TranslatorConfig(provider="fake"),
    )
    pipeline = Pipeline(cfg, FakeAsr(), FakeTranslator(), FakeTts())
    assert await _run_turn(pipeline, room_noise(3)) == []
