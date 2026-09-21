"""End-to-end pipeline integration test: VAD -> ASR -> segmenter -> translator.

Uses the real (bundled, offline) Silero VAD model but fake ASR/Translator
providers, so this test is fast and needs no model download or network
access - it validates wiring/event-ordering/schema, not ASR accuracy (that's
what bench/benchmark.py + a real ASR provider are for).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from app.audio_utils import iter_frames, load_wav_as_pcm16_mono
from app.config import AsrConfig, EngineConfig, TranslatorConfig
from app.pipeline import Pipeline
from app.providers.asr_fake import FakeAsr
from app.providers.translator_fake import FakeTranslator
from app.schema import EventType, PipelineEvent


@pytest.fixture
def engine_cfg() -> EngineConfig:
    return EngineConfig(asr=AsrConfig(provider="fake"), translator=TranslatorConfig(provider="fake"))


async def _run_wav_through_pipeline(
    cfg: EngineConfig, asr: FakeAsr, translator: FakeTranslator, wav_path: Path
) -> tuple[Pipeline, list[PipelineEvent]]:
    pipeline = Pipeline(cfg, asr, translator)
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
