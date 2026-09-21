"""Shared console formatting for the CLI harnesses' streaming event output."""

from __future__ import annotations

from app.schema import EventType, PipelineEvent

_LABELS = {
    EventType.PARTIAL: "PARTIAL",
    EventType.FINAL: "FINAL",
    EventType.TRANSLATION: "TRANSL",
    EventType.AUDIO: "AUDIO",
    EventType.ERROR: "ERROR",
}


def format_event(event: PipelineEvent) -> str:
    label = _LABELS.get(event.type, str(event.type)).ljust(7)
    latency = f"{event.latency_ms:7.1f}ms" if event.latency_ms is not None else " " * 9
    suffix = ""
    if event.type is EventType.AUDIO and event.audio:
        duration_s = len(event.audio) / 2 / (event.audio_sample_rate or 1)
        suffix = f"  ({duration_s:.2f}s audio @ {event.audio_sample_rate}Hz)"
    return f"[{label}] [{event.lang}] [{latency}] {event.text}{suffix}"
