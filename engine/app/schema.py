"""Wire event schema streamed over the WebSocket (and reused by the CLI/bench harnesses).

One PipelineEvent per JSON text WS message:
    {type, lang, text, turn_id, latency_ms, ...}
"""

from __future__ import annotations

import time
import uuid
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class EventType(str, Enum):
    PARTIAL = "partial"  # streaming ASR hypothesis for the open turn, not yet stable
    FINAL = "final"  # endpointed ASR transcript for a completed segment/turn
    TRANSLATION = "translation"  # translated text for a (segment of a) turn
    AUDIO = "audio"  # synthesized speech for one translated sentence
    ERROR = "error"  # pipeline/provider error surfaced to the client


def new_turn_id() -> str:
    return uuid.uuid4().hex[:12]


class PipelineEvent(BaseModel):
    """A single event in the ASR -> segmenter -> translator -> TTS pipeline.

    `latency_ms` is the wall-clock time from end-of-speech (VAD endpoint) to
    this event being emitted, so clients and the benchmark script can check
    budget compliance (<=2s captions, <=3.5s audio) without a side channel.
    """

    # Note: no use_enum_values - `.type` stays a real EventType member in memory
    # (so `event.type is EventType.FINAL` works), while JSON serialization still
    # emits its plain string value ("final") either way.
    model_config = ConfigDict(ser_json_bytes="base64")

    type: EventType
    lang: str = Field(description="Language code of `text`, e.g. 'en', 'ar'")
    text: str = ""
    turn_id: str
    seq: int = Field(default=0, description="Monotonic event index within the turn")
    latency_ms: float | None = None
    is_final_segment: bool = False
    audio: bytes | None = Field(default=None, description="PCM16 mono audio, set on AUDIO events")
    audio_sample_rate: int | None = Field(default=None, description="Sample rate of `audio`, Hz")
    timestamp: float = Field(default_factory=time.time)
    error: str | None = None
