"""The neural voices, and the local voices taking over when they can't deliver."""

from __future__ import annotations

import io
import sys
import types

import numpy as np
import pytest
import soundfile as sf

from app.config import TtsConfig
from app.providers import tts_neural
from app.providers.base import TtsAudio, TtsProvider
from app.providers.tts_neural import NeuralVoiceTts, decode_audio


def _mp3(seconds: float = 0.5, rate: int = 24000) -> bytes:
    tone = (np.sin(np.linspace(0, 2 * np.pi * 220 * seconds, int(rate * seconds))) * 0.3).astype(np.float32)
    buffer = io.BytesIO()
    sf.write(buffer, tone, rate, format="MP3")
    return buffer.getvalue()


class LocalVoice(TtsProvider):
    def __init__(self) -> None:
        self.spoken: list[tuple[str, str]] = []

    async def synthesize(self, text: str, lang: str) -> TtsAudio:
        self.spoken.append((text, lang))
        return TtsAudio(pcm16=b"\x01\x00" * 100, sample_rate=16000)


def _fake_edge(monkeypatch: pytest.MonkeyPatch, chunks: list[dict] | Exception) -> list[tuple]:
    calls: list[tuple] = []

    class Communicate:
        def __init__(self, text: str, voice: str, rate: str = "+0%") -> None:
            calls.append((text, voice, rate))

        async def stream(self):
            if isinstance(chunks, Exception):
                raise chunks
            for chunk in chunks:
                yield chunk

    monkeypatch.setitem(sys.modules, "edge_tts", types.SimpleNamespace(Communicate=Communicate))
    return calls


def test_mp3_from_the_service_decodes_to_pcm16() -> None:
    audio = decode_audio(_mp3(0.5))
    assert audio.sample_rate == 24000
    assert abs(len(audio.pcm16) // 2 - 12000) < 2400  # ~0.5s, give or take MP3 padding


async def test_the_voice_for_each_language(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_edge(monkeypatch, [{"type": "WordBoundary"}, {"type": "audio", "data": _mp3()}])
    local = LocalVoice()
    tts = NeuralVoiceTts(TtsConfig(provider="neural"), fallback_factory=lambda: local)
    audio = await tts.synthesize("مرحبا بكم", "ar")
    await tts.synthesize("Welcome", "en")
    assert len(audio.pcm16) > 0
    assert [c[1:] for c in calls] == [("ar-JO-TaimNeural", "-10%"), ("en-US-AndrewNeural", "-15%")]
    assert local.spoken == []  # never loaded while the service works


async def test_local_voice_takes_over_and_outages_pause_the_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tts_neural, "spoken", {"neural": 0, "local": 0})
    calls = _fake_edge(monkeypatch, ConnectionError("offline"))
    local = LocalVoice()
    tts = NeuralVoiceTts(TtsConfig(provider="neural"), fallback_factory=lambda: local)
    for i in range(tts_neural.FAILURES_BEFORE_PAUSE + 2):
        await tts.synthesize(f"sentence {i}", "en")
    assert len(local.spoken) == tts_neural.FAILURES_BEFORE_PAUSE + 2  # every sentence still spoken
    assert len(calls) == tts_neural.FAILURES_BEFORE_PAUSE  # then the service is left alone for a while
    assert tts_neural.spoken == {"neural": 0, "local": len(local.spoken)}  # what the dashboard reports
    assert "offline" in (tts_neural.last_error or "")
