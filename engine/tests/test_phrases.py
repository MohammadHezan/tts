"""Phrase by phrase: long speech handed on at short pauses (vad.phrase_min_ms),
translated and spoken in a queue behind the listening (Pipeline background=True),
and a meeting that keeps being heard while the bot talks (half_duplex=False)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import numpy as np

from app.attendee_bridge import ATTENDEE_SAMPLE_RATE, BotEventHub, run_bridge
from app.audio_utils import load_wav_as_pcm16_mono
from app.config import AsrConfig, EngineConfig, TranslatorConfig, VadConfig
from app.pipeline import Pipeline
from app.providers.asr_fake import FakeAsr
from app.providers.base import AsrHypothesis, AsrProvider, TranslatorProvider, TurnContext
from app.providers.tts_fake import FakeTts
from app.schema import EventType, PipelineEvent
from app.vad import MODEL_WINDOW_SAMPLES, SpeechEndpointer, VadEvent, VadEventType
from tests.test_meeting_audio_guards import BOT_ID, SPOKEN, ScriptedSocket, _finals, _mixed

W = MODEL_WINDOW_SAMPLES


class PausingIterator:
    """Stand-in for silero_vad.VADIterator: speech from window 0, quiet during
    `pauses` (window indexes; temp_end marks where the quiet began, as the real
    one does), and the utterance's end at `end_window`."""

    def __init__(self, pauses: list[range], end_window: int | None = None, speech_pad: int = 3200) -> None:
        self.pauses = pauses
        self.end_window = end_window
        self.speech_pad = speech_pad
        self.temp_end = 0
        self.current_sample = 0
        self.calls = 0

    def __call__(self, x: np.ndarray, return_seconds: bool = False) -> dict[str, int] | None:
        i = self.calls
        self.calls += 1
        self.current_sample += W
        if i == 0:
            return {"start": 0}
        if any(i in pause for pause in self.pauses):
            self.temp_end = self.temp_end or self.current_sample
        else:
            self.temp_end = 0
        if i == self.end_window:
            end = self.temp_end + self.speech_pad - W
            self.temp_end = 0
            return {"end": end}
        return None

    def reset_states(self) -> None:
        pass


def _push_windows(ep: SpeechEndpointer, audio: np.ndarray) -> list[VadEvent]:
    events: list[VadEvent] = []
    for start in range(0, len(audio) - W + 1, W):
        events.extend(ep.push(audio[start : start + W].tobytes()))
    return events


def test_a_short_pause_ends_a_phrase_once_it_is_long_enough() -> None:
    cfg = VadConfig(phrase_min_ms=1000, phrase_pause_ms=250, min_speech_ms=150)
    # Pause at window 10 (too early), at 40 (a phrase), and the end at 80 onwards.
    iterator = PausingIterator(pauses=[range(10, 20), range(40, 50), range(80, 200)], end_window=99)
    ep = SpeechEndpointer(cfg=cfg, sample_rate=16000, iterator=iterator)
    events = _push_windows(ep, np.ones(120 * W, dtype=np.int16))

    assert [e.type for e in events] == [
        VadEventType.SPEECH_START,
        VadEventType.SPEECH_END,  # the phrase before the pause at window 40
        VadEventType.SPEECH_START,
        VadEventType.SPEECH_END,  # the rest, handed on at the next pause - before the utterance's own end
        VadEventType.SPEECH_START,  # nothing more is said: never becomes an utterance
    ]
    quiet_from = 41 * W  # where the iterator's quiet started (end of window 40)
    first, second = events[1], events[3]
    assert first.sample_pos == quiet_from + 3200  # the quiet start, plus the usual padding
    assert first.audio is not None and len(first.audio) == first.sample_pos
    assert events[2].sample_pos == first.sample_pos  # the next phrase starts exactly there
    assert second.audio is not None and len(second.audio) == second.sample_pos - first.sample_pos


def test_whole_utterances_when_phrases_are_off() -> None:
    iterator = PausingIterator(pauses=[range(40, 50), range(80, 200)], end_window=99)
    ep = SpeechEndpointer(cfg=VadConfig(min_speech_ms=150), sample_rate=16000, iterator=iterator)
    events = _push_windows(ep, np.ones(120 * W, dtype=np.int16))
    assert [e.type for e in events] == [VadEventType.SPEECH_START, VadEventType.SPEECH_END]


def test_without_a_pause_a_phrase_is_cut_at_the_quietest_moment() -> None:
    cfg = VadConfig(phrase_min_ms=1000, phrase_max_ms=2000, min_speech_ms=150)
    ep = SpeechEndpointer(cfg=cfg, sample_rate=16000, iterator=PausingIterator(pauses=[]))
    audio = (np.random.default_rng(3).standard_normal(3 * 16000) * 3000).astype(np.int16)
    gap = slice(int(1.6 * 16000), int(1.6 * 16000) + 960)  # 60ms between two words
    audio[gap] = 0
    events = _push_windows(ep, audio)

    ends = [e for e in events if e.type is VadEventType.SPEECH_END]
    assert ends, "a 3s phrase without a pause was never cut"
    assert gap.start <= ends[0].sample_pos <= gap.stop


def test_real_silero_hands_on_the_first_phrase_at_a_pause(synthesized_wav: Callable[[str], Path]) -> None:
    speech = load_wav_as_pcm16_mono(synthesized_wav("We would like to order twenty dining chairs in walnut wood"))
    pause = np.zeros(int(0.4 * 16000), dtype=np.int16)  # a breath between phrases, shorter than min_silence_ms
    audio = np.concatenate([speech, pause, speech, np.zeros(16000, dtype=np.int16)])
    cfg = VadConfig(phrase_min_ms=int(len(speech) / 16) - 500, min_silence_ms=700)
    ep = SpeechEndpointer(cfg=cfg, sample_rate=16000)  # the real bundled model
    events = _push_windows(ep, audio)
    events += ep.flush()

    ends = [e for e in events if e.type is VadEventType.SPEECH_END]
    assert len(ends) == 2  # without phrases: one, after the second half
    voice_ends = int(np.flatnonzero(np.abs(speech) > 500)[-1])  # espeak's file has its own silence after
    assert voice_ends <= ends[0].sample_pos <= len(speech) + len(pause)


# --- the queue ----------------------------------------------------------------


class ScriptedAsr(AsrProvider):
    """Each utterance is the next of `texts`."""

    def __init__(self, texts: list[tuple[str, str]]) -> None:
        self._texts = list(texts)

    def start_utterance(self, turn_id: str) -> None:
        pass

    async def feed(self, utterance_audio_so_far: np.ndarray) -> AsrHypothesis | None:
        return None

    async def finalize(self, full_utterance_audio: np.ndarray) -> AsrHypothesis:
        text, lang = self._texts.pop(0)
        return AsrHypothesis(text=text, language=lang, is_final=True)


class GatedTranslator(TranslatorProvider):
    """Holds each translation until `release` is set, and records what it got."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.calls: list[tuple[str, list[TurnContext] | None]] = []

    async def translate(self, text: str, source_lang: str, target_lang: str, context: list[TurnContext] | None = None) -> str:
        self.calls.append((text, context))
        await self.release.wait()
        return f"<{target_lang}> {text}"


class TaggingTranslator(TranslatorProvider):
    async def translate(self, text: str, source_lang: str, target_lang: str, context: list[TurnContext] | None = None) -> str:
        return f"<{target_lang}> {text}"


LOUD = (np.random.default_rng(1).standard_normal(16000) * 3000).astype(np.int16)


async def _say(pipeline: Pipeline) -> list[PipelineEvent]:
    events = []
    async for event in pipeline._handle_vad_event(VadEvent(VadEventType.SPEECH_START, 0)):
        events.append(event)
    async for event in pipeline._handle_vad_event(VadEvent(VadEventType.SPEECH_END, len(LOUD), audio=LOUD)):
        events.append(event)
    return events


async def _drain(pipeline: Pipeline) -> list[PipelineEvent]:
    collected = asyncio.create_task(_collect(pipeline))
    async for _ in pipeline.flush():
        pass
    return await collected


async def _collect(pipeline: Pipeline) -> list[PipelineEvent]:
    return [event async for event in pipeline.background_events()]


def _cfg() -> EngineConfig:
    return EngineConfig(asr=AsrConfig(provider="fake"), translator=TranslatorConfig(provider="fake"))


async def test_listening_goes_on_while_earlier_phrases_are_translated() -> None:
    texts = [("We would like to order", "en"), ("twenty dining chairs", "en"), ("in walnut wood.", "en")]
    translator = GatedTranslator()
    pipeline = Pipeline(_cfg(), ScriptedAsr(texts), translator, FakeTts(), background=True)

    heard = []
    for _ in texts:
        heard.append(await _say(pipeline))
        await asyncio.sleep(0)  # a live call's next frame: the queue picks up the first phrase
    # Every phrase was transcribed while the first was still being translated...
    assert [[e.type for e in events] for events in heard] == [[EventType.FINAL]] * 3
    assert [text for text, _ in translator.calls] == ["We would like to order"]

    translator.release.set()
    out = await _drain(pipeline)
    # ...and the two that queued up behind it went to the translator together.
    assert [text for text, _ in translator.calls] == ["We would like to order", "twenty dining chairs in walnut wood."]
    said = [e for e in out if e.type is EventType.TRANSLATION]
    assert [e.text for e in said] == ["<ar> We would like to order", "<ar> twenty dining chairs in walnut wood."]
    assert said[1].turn_id == heard[2][0].turn_id  # shown with the last phrase it covers
    assert [e.type for e in out if e.type is EventType.AUDIO] == [EventType.AUDIO] * 2
    # The second translation saw the first one as context.
    context = translator.calls[1][1]
    assert context and context[-1].source_text == "We would like to order"


async def test_queued_phrases_in_another_language_are_not_merged() -> None:
    texts = [("Hello.", "en"), ("مرحبا بكم", "ar"), ("How are you?", "en")]
    translator = GatedTranslator()
    pipeline = Pipeline(_cfg(), ScriptedAsr(texts), translator, FakeTts(), background=True)
    for _ in texts:
        await _say(pipeline)
    translator.release.set()
    out = await _drain(pipeline)
    assert [text for text, _ in translator.calls] == ["Hello.", "مرحبا بكم", "How are you?"]
    assert [e.lang for e in out if e.type is EventType.TRANSLATION] == ["ar", "en", "ar"]


async def test_one_failed_phrase_does_not_stop_the_queue() -> None:
    class FlakyTranslator(TranslatorProvider):
        def __init__(self) -> None:
            self.n = 0

        async def translate(self, text: str, source_lang: str, target_lang: str, context: list[TurnContext] | None = None) -> str:
            self.n += 1
            if self.n == 1:
                raise TimeoutError("slow")
            return f"<{target_lang}> {text}"

    pipeline = Pipeline(_cfg(), ScriptedAsr([("One.", "en"), ("Two.", "ar")]), FlakyTranslator(), FakeTts(), background=True)
    collector = asyncio.create_task(_collect(pipeline))
    await _say(pipeline)
    await asyncio.sleep(0.05)  # the first is translated (and fails) before the second is heard
    await _say(pipeline)
    async for _ in pipeline.flush():
        pass
    out = await collector
    assert [e.type for e in out if e.type is not EventType.AUDIO] == [EventType.ERROR, EventType.TRANSLATION]


async def test_flush_with_nothing_said_ends_the_background_events() -> None:
    pipeline = Pipeline(_cfg(), FakeAsr(), GatedTranslator(), FakeTts(), background=True)
    assert await asyncio.wait_for(_drain(pipeline), timeout=2) == []


# --- the meeting ----------------------------------------------------------------


async def test_full_duplex_hears_people_who_talk_while_the_bot_speaks(synthesized_wav: Callable[[str], Path]) -> None:
    cfg = _cfg()
    speech = load_wav_as_pcm16_mono(synthesized_wav(SPOKEN), target_sample_rate=ATTENDEE_SAMPLE_RATE)
    silence = np.zeros(int(ATTENDEE_SAMPLE_RATE * 1.5), dtype=np.int16)
    # Someone carries on talking while the bot speaks the first part.
    socket = ScriptedSocket(_mixed(np.concatenate([speech, silence])), _mixed(np.concatenate([speech, silence])))
    hub = BotEventHub()
    captions = hub.subscribe(BOT_ID)

    await run_bridge(
        socket,  # type: ignore[arg-type]
        lambda **hooks: Pipeline(cfg, FakeAsr(final_text=SPOKEN), TaggingTranslator(), FakeTts(), **hooks),
        cfg.audio.sample_rate_hz,
        cfg.audio.frame_samples * 2,
        hub,
        half_duplex=False,
    )
    assert socket.sent, "the bot never spoke"
    assert len(_finals(captions)) == 2  # half-duplex would have heard only the first
