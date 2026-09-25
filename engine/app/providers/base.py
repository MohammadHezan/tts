"""Provider interfaces for ASR / Translator / TTS + factories reading config.yaml.

Every concrete provider (a local model or a cloud API) implements one of these
ABCs, so pipeline.py never depends on a specific vendor. Swap local <-> cloud
by editing config.yaml - no pipeline code changes. Heavy imports (faster-whisper,
httpx clients, ...) are deferred into each provider module so importing this
file, or selecting the "fake" provider for tests, never pulls in ML deps.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np

from app.config import AsrConfig, TranslatorConfig, TtsConfig


@dataclass
class AsrHypothesis:
    text: str
    language: str
    is_final: bool = False


class AsrProvider(ABC):
    """One instance per pipeline/connection. start_utterance() resets per-turn state."""

    @abstractmethod
    def start_utterance(self, turn_id: str) -> None: ...

    @abstractmethod
    async def feed(self, utterance_audio_so_far: np.ndarray) -> AsrHypothesis | None:
        """Called while an utterance is still open (pre VAD speech_end).

        `utterance_audio_so_far` is the full int16 PCM buffer of the utterance
        from its start up to now. Returns an updated partial hypothesis, or
        None if not enough new audio has accumulated to justify a re-decode.
        """

    @abstractmethod
    async def finalize(self, full_utterance_audio: np.ndarray) -> AsrHypothesis:
        """Called once at VAD speech_end with the complete utterance audio."""


@dataclass
class TurnContext:
    """One prior conversational turn, for translator context windows (Phase 2)."""

    source_text: str
    translated_text: str
    source_lang: str = "en"
    target_lang: str = "ar"


class TranslatorProvider(ABC):
    @abstractmethod
    async def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        context: list[TurnContext] | None = None,
    ) -> str: ...


@dataclass
class TtsAudio:
    pcm16: bytes
    sample_rate: int


class TtsProvider(ABC):
    """Synthesizes one sentence of text into speech. Called once per translated
    sentence (the same granularity as the translator), immediately after its
    TRANSLATION event, so audio starts streaming without waiting for the whole
    utterance's translation to finish.
    """

    @abstractmethod
    async def synthesize(self, text: str, lang: str) -> TtsAudio: ...


def build_asr_provider(cfg: AsrConfig) -> AsrProvider:
    if cfg.provider == "faster_whisper":
        from app.providers.asr_faster_whisper import FasterWhisperAsr

        return FasterWhisperAsr(cfg)
    if cfg.provider == "fake":
        from app.providers.asr_fake import FakeAsr

        return FakeAsr()
    raise ValueError(f"Unknown ASR provider: {cfg.provider!r}")


def build_translator_provider(cfg: TranslatorConfig) -> TranslatorProvider:
    if cfg.provider == "ollama":
        from app.providers.translator_ollama import OllamaTranslator

        return OllamaTranslator(cfg)
    if cfg.provider == "claude":
        from app.providers.translator_claude import ClaudeTranslator

        return ClaudeTranslator(cfg)
    if cfg.provider == "fake":
        from app.providers.translator_fake import FakeTranslator

        return FakeTranslator()
    raise ValueError(f"Unknown translator provider: {cfg.provider!r}")


def build_tts_provider(cfg: TtsConfig) -> TtsProvider | None:
    """Returns None for provider="none" (text captions only, no audio) - Pipeline
    treats that as "skip TTS" rather than needing a null-object implementation.
    """
    if cfg.provider == "none":
        return None
    if cfg.provider == "multi_voice":
        from app.providers.tts_multi import MultiVoiceTts

        return MultiVoiceTts(cfg)
    if cfg.provider == "neural":
        from app.providers.tts_multi import MultiVoiceTts
        from app.providers.tts_neural import NeuralVoiceTts

        return NeuralVoiceTts(cfg, fallback_factory=lambda: MultiVoiceTts(cfg))
    if cfg.provider == "fake":
        from app.providers.tts_fake import FakeTts

        return FakeTts()
    raise ValueError(f"Unknown TTS provider: {cfg.provider!r}")
