"""End-to-end pipeline integration test: VAD -> ASR -> segmenter -> translator.

Uses the real (bundled, offline) Silero VAD model but fake ASR/Translator
providers, so this test is fast and needs no model download or network
access - it validates wiring/event-ordering/schema, not ASR accuracy (that's
what bench/benchmark.py + a real ASR provider are for).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from app.audio_utils import iter_frames, load_wav_as_pcm16_mono
from app.config import AsrConfig, EngineConfig, TranslatorConfig
from app.pipeline import Pipeline
from app.providers.asr_fake import FakeAsr
from app.providers.base import TtsProvider
from app.providers.translator_fake import FakeTranslator
from app.providers.tts_fake import FakeTts
from app.schema import EventType, PipelineEvent


@pytest.fixture
def engine_cfg() -> EngineConfig:
    return EngineConfig(asr=AsrConfig(provider="fake"), translator=TranslatorConfig(provider="fake"))


async def _run_wav_through_pipeline(
    cfg: EngineConfig,
    asr: FakeAsr,
    translator: FakeTranslator,
    wav_path: Path,
    tts: TtsProvider | None = None,
    pipeline: Pipeline | None = None,
) -> tuple[Pipeline, list[PipelineEvent]]:
    pipeline = pipeline or Pipeline(cfg, asr, translator, tts)
    events: list[PipelineEvent] = []
    pcm = load_wav_as_pcm16_mono(wav_path, target_sample_rate=cfg.audio.sample_rate_hz)
    for frame in iter_frames(pcm, cfg.audio.frame_samples):
        async for event in pipeline.process_frame(frame.tobytes()):
            events.append(event)
    async for event in pipeline.flush():
        events.append(event)
    return pipeline, events


async def test_pipeline_emits_partial_final_translation_in_order(
    engine_cfg: EngineConfig, synthesized_wav: Callable[[str], Path]
) -> None:
    text = "Good afternoon, thank you for visiting our showroom today."
    wav_path = synthesized_wav(text)
    asr = FakeAsr(final_text=text)
    translator = FakeTranslator()

    _, events = await _run_wav_through_pipeline(engine_cfg, asr, translator, wav_path)

    types = [e.type for e in events]
    assert EventType.PARTIAL in types
    assert types.count(EventType.FINAL) == 1
    assert types.count(EventType.TRANSLATION) >= 1

    final_event = next(e for e in events if e.type is EventType.FINAL)
    assert final_event.text == text
    assert final_event.lang == "en"

    translation_events = [e for e in events if e.type is EventType.TRANSLATION]
    assert all(e.lang == "ar" for e in translation_events)
    assert all(e.text.startswith("[AR]") for e in translation_events)

    # every event in this single continuous utterance shares one turn_id
    assert len({e.turn_id for e in events}) == 1

    # seq is monotonically increasing within the turn
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs)
    assert len(seqs) == len(set(seqs))

    assert translator.calls  # the translator was actually invoked


async def test_pipeline_events_are_json_serializable(
    engine_cfg: EngineConfig, synthesized_wav: Callable[[str], Path]
) -> None:
    text = "Please confirm the shipment."
    wav_path = synthesized_wav(text)
    asr = FakeAsr(final_text=text)
    translator = FakeTranslator()

    _, events = await _run_wav_through_pipeline(engine_cfg, asr, translator, wav_path)
    assert events
    for event in events:
        assert '"type"' in event.model_dump_json()


async def test_latency_for_turn_reports_asr_and_translate_stages(
    engine_cfg: EngineConfig, synthesized_wav: Callable[[str], Path]
) -> None:
    text = "The delivery date is confirmed."
    wav_path = synthesized_wav(text)
    asr = FakeAsr(final_text=text)
    translator = FakeTranslator()

    pipeline, events = await _run_wav_through_pipeline(engine_cfg, asr, translator, wav_path)
    final_event = next(e for e in events if e.type is EventType.FINAL)

    latencies = pipeline.latency_for_turn(final_event.turn_id)
    assert latencies is not None
    assert "asr_final" in latencies
    assert any(key.startswith("translate") for key in latencies)


async def test_final_latency_is_measured_from_speech_end(
    engine_cfg: EngineConfig, synthesized_wav: Callable[[str], Path]
) -> None:
    text = "Twelve units are in stock."
    wav_path = synthesized_wav(text)
    asr = FakeAsr(final_text=text)
    translator = FakeTranslator()

    _, events = await _run_wav_through_pipeline(engine_cfg, asr, translator, wav_path)

    final_event = next(e for e in events if e.type is EventType.FINAL)
    translation_event = next(e for e in events if e.type is EventType.TRANSLATION)
    assert final_event.latency_ms is not None
    assert translation_event.latency_ms is not None
    # translation happens after the final transcript, so it should never be faster
    assert translation_event.latency_ms >= final_event.latency_ms


async def test_tts_emits_audio_event_after_each_translation(
    engine_cfg: EngineConfig, synthesized_wav: Callable[[str], Path]
) -> None:
    text = "The delivery is confirmed for Friday."
    wav_path = synthesized_wav(text)
    asr = FakeAsr(final_text=text)
    translator = FakeTranslator()
    tts = FakeTts()

    _, events = await _run_wav_through_pipeline(engine_cfg, asr, translator, wav_path, tts=tts)

    translation_events = [e for e in events if e.type is EventType.TRANSLATION]
    audio_events = [e for e in events if e.type is EventType.AUDIO]
    assert len(audio_events) == len(translation_events) > 0

    for translation_event, audio_event in zip(translation_events, audio_events, strict=True):
        assert audio_event.turn_id == translation_event.turn_id
        assert audio_event.lang == translation_event.lang
        assert audio_event.text == translation_event.text
        assert audio_event.audio  # non-empty PCM16 bytes
        assert audio_event.audio_sample_rate == 16000
        # TTS runs after translation within the same sentence, so it can only be equal or later
        assert audio_event.latency_ms >= translation_event.latency_ms


async def test_no_audio_events_when_tts_is_none(
    engine_cfg: EngineConfig, synthesized_wav: Callable[[str], Path]
) -> None:
    text = "No audio should be produced here."
    wav_path = synthesized_wav(text)
    asr = FakeAsr(final_text=text)
    translator = FakeTranslator()

    _, events = await _run_wav_through_pipeline(engine_cfg, asr, translator, wav_path, tts=None)
    assert not any(e.type is EventType.AUDIO for e in events)


async def test_context_memory_accumulates_across_turns(
    engine_cfg: EngineConfig, synthesized_wav: Callable[[str], Path]
) -> None:
    translator = FakeTranslator()
    pipeline = Pipeline(engine_cfg, FakeAsr(final_text="First sentence here."), translator)

    wav_1 = synthesized_wav("First sentence here.")
    await _run_wav_through_pipeline(engine_cfg, None, None, wav_1, pipeline=pipeline)  # type: ignore[arg-type]
    assert translator.contexts[-1] == []  # no prior turns yet

    pipeline._asr = FakeAsr(final_text="Second sentence here.")  # noqa: SLF001 - swap ASR text for turn 2
    wav_2 = synthesized_wav("Second sentence here.")
    await _run_wav_through_pipeline(engine_cfg, None, None, wav_2, pipeline=pipeline)  # type: ignore[arg-type]

    second_call_context = translator.contexts[-1]
    assert second_call_context is not None
    assert len(second_call_context) == 1
    assert second_call_context[0].source_text == "First sentence here."
    assert second_call_context[0].translated_text == "[AR] First sentence here."


async def test_ar_to_en_direction_resolves_correctly(
    engine_cfg: EngineConfig, synthesized_wav: Callable[[str], Path]
) -> None:
    # engine_cfg's translator pair is source_lang=en/target_lang=ar (the default);
    # when ASR detects Arabic instead, Pipeline should translate ar -> en.
    text = "مرحبا بكم في صالة العرض"
    wav_path = synthesized_wav("Hello there")  # audio content is irrelevant with FakeAsr
    asr = FakeAsr(final_text=text, language="ar")
    translator = FakeTranslator()

    _, events = await _run_wav_through_pipeline(engine_cfg, asr, translator, wav_path)

    final_event = next(e for e in events if e.type is EventType.FINAL)
    translation_event = next(e for e in events if e.type is EventType.TRANSLATION)
    assert final_event.lang == "ar"
    assert translation_event.lang == "en"
    assert translator.calls[-1] == text


class _TimingOutTranslator(FakeTranslator):
    """Fails the first call the way a slow Ollama does, then recovers."""

    async def translate(self, text: str, source_lang: str, target_lang: str, context=None) -> str:  # type: ignore[no-untyped-def]
        if not self.calls:
            self.calls.append(text)
            raise httpx.ReadTimeout("")
        return await super().translate(text, source_lang, target_lang, context)


async def test_failed_translation_is_an_error_event_and_the_next_turn_still_works(
    engine_cfg: EngineConfig, synthesized_wav: Callable[[str], Path]
) -> None:
    # Raised instead, it ended the caller's session: a meeting bot went deaf
    # for the rest of the call after one slow translation.
    translator = _TimingOutTranslator()
    pipeline = Pipeline(engine_cfg, FakeAsr(final_text="First sentence here."), translator, FakeTts())

    _, first = await _run_wav_through_pipeline(
        engine_cfg, None, None, synthesized_wav("First sentence here."), pipeline=pipeline  # type: ignore[arg-type]
    )
    errors = [e for e in first if e.type is EventType.ERROR]
    assert len(errors) == 1
    assert errors[0].error == "translation failed: ReadTimeout"
    assert not any(e.type in (EventType.TRANSLATION, EventType.AUDIO) for e in first)

    _, second = await _run_wav_through_pipeline(
        engine_cfg, None, None, synthesized_wav("Second sentence here."), pipeline=pipeline  # type: ignore[arg-type]
    )
    assert [e.text for e in second if e.type is EventType.TRANSLATION] == ["[AR] First sentence here."]
    assert any(e.type is EventType.AUDIO for e in second)


async def test_pipeline_without_partials_still_transcribes(
    engine_cfg: EngineConfig, synthesized_wav: Callable[[str], Path]
) -> None:
    text = "Meeting bots skip partial captions."
    pipeline = Pipeline(engine_cfg, FakeAsr(final_text=text), FakeTranslator(), emit_partials=False)
    _, events = await _run_wav_through_pipeline(engine_cfg, None, None, synthesized_wav(text), pipeline=pipeline)  # type: ignore[arg-type]
    assert not any(e.type is EventType.PARTIAL for e in events)
    assert [e.text for e in events if e.type is EventType.FINAL] == [text]
