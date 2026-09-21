"""Tests for the wire event schema (app/schema.py)."""

from __future__ import annotations

import json

from app.schema import EventType, PipelineEvent, new_turn_id


def test_new_turn_id_is_unique_and_short() -> None:
    a, b = new_turn_id(), new_turn_id()
    assert a != b
    assert len(a) == 12


def test_event_type_stays_a_real_enum_member() -> None:
    # Regression guard: pydantic's use_enum_values=True would silently turn
    # `.type` into a plain str, breaking `event.type is EventType.X` checks
    # used elsewhere (e.g. bench/benchmark.py).
    event = PipelineEvent(type=EventType.FINAL, lang="en", text="hi", turn_id=new_turn_id())
    assert event.type is EventType.FINAL


def test_json_round_trip_uses_plain_string_type() -> None:
    event = PipelineEvent(type=EventType.TRANSLATION, lang="ar", text="مرحبا", turn_id="abc123", latency_ms=42.5)
    payload = json.loads(event.model_dump_json())
    assert payload["type"] == "translation"
    assert payload["lang"] == "ar"
    assert payload["text"] == "مرحبا"
    assert payload["latency_ms"] == 42.5


def test_audio_bytes_serialize_as_base64() -> None:
    event = PipelineEvent(type=EventType.AUDIO, lang="ar", text="", turn_id="abc123", audio=b"\x00\x01\x02")
    payload = json.loads(event.model_dump_json())
    assert payload["audio"] == "AAEC"


def test_defaults() -> None:
    event = PipelineEvent(type=EventType.PARTIAL, lang="en", text="hello", turn_id="t1")
    assert event.seq == 0
    assert event.is_final_segment is False
    assert event.latency_ms is None
    assert event.error is None
