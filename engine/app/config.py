"""Typed loader for config.yaml + .env.

Every pipeline stage (VAD / ASR / Translator / TTS) is configured here by
"provider" name. Provider factories in app/providers/base.py read this config
to decide which concrete implementation to instantiate, so swapping local <->
cloud never touches pipeline code. No secrets live in config.yaml - only
provider *names* and non-sensitive parameters; API keys come from .env via
os.environ (see .env.example).
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field


def _compute_repo_root() -> Path:
    if getattr(sys, "frozen", False):
        # PyInstaller onedir build (see engine/desktop_launcher.spec): data
        # files (config.yaml, glossary.yaml, app/static/) are bundled
        # alongside the executable, not three parents up from this file.
        return Path(sys.executable).resolve().parent
    # app/config.py -> app/ -> engine/ -> repo root
    return Path(__file__).resolve().parents[2]


REPO_ROOT = _compute_repo_root()


class AudioConfig(BaseModel):
    sample_rate_hz: int = 16000
    frame_ms: int = 30
    channels: int = 1

    @property
    def frame_samples(self) -> int:
        return int(self.sample_rate_hz * self.frame_ms / 1000)


class LocalAgreementConfig(BaseModel):
    enabled: bool = True
    chunk_ms: int = 500
    agreement_window: int = 2


class VadConfig(BaseModel):
    provider: Literal["silero"] = "silero"
    threshold: float = 0.5
    min_speech_ms: int = 150
    min_silence_ms: int = 700
    speech_pad_ms: int = 200
    # Utterances quieter than this (RMS, dBFS) are dropped before ASR - a
    # call's comfort noise or a distant sound, not someone talking. None = off.
    min_utterance_dbfs: float | None = None
    # Utterances with less voiced sound than this (vocal cords vibrating - see
    # app/voicing.py) are dropped before ASR: breaths, clicks, a chair. None = off.
    min_voiced_ms: int | None = None
    # Phrase by phrase: once someone has been talking for phrase_min_ms, the
    # next pause of phrase_pause_ms ends a phrase (~10 words) that is
    # translated while they carry on; past phrase_max_ms without such a pause,
    # the phrase ends at the quietest moment of the last second. None = whole
    # utterances only (they end after min_silence_ms of silence).
    phrase_min_ms: int | None = None
    phrase_pause_ms: int = 250
    phrase_max_ms: int | None = 8000


class AsrConfig(BaseModel):
    provider: Literal["faster_whisper", "fake"] = "faster_whisper"
    model: str = "large-v3-turbo"
    device: Literal["auto", "cuda", "cpu"] = "auto"
    compute_type: str = "auto"
    # If the GPU can't run the model (driver or CUDA libraries missing), load
    # this one on the CPU instead of failing. None = fail.
    cpu_fallback_model: str | None = None
    # The glossary's terms, in the language being heard, are given to Whisper
    # as hint words ("walnut" is otherwise easily heard as "wall at"). Same
    # file as translator.glossary_path. None = no hints.
    glossary_path: str | None = None
    language: str = "auto"
    # With language: auto, only these languages are considered (the most likely
    # of them wins). Empty = anything Whisper knows. Stops a short English turn
    # being "detected" as Dutch, or Jordanian Arabic as Persian, which would
    # flip the translation direction the wrong way.
    candidate_languages: list[str] = Field(default_factory=list)
    beam_size: int = 1
    local_agreement: LocalAgreementConfig = Field(default_factory=LocalAgreementConfig)


class OllamaConfig(BaseModel):
    base_url: str = "http://localhost:11434"
    model: str = "llama3.1:8b-instruct-q4_K_M"
    timeout_s: float = 15.0
    # How long Ollama keeps the model in memory after a request. Its own default
    # (5m) unloads it during a quiet stretch of a meeting, and reloading an 8B
    # model on CPU adds tens of seconds to the next sentence.
    keep_alive: str = "30m"


class ClaudeConfig(BaseModel):
    model: str = "claude-haiku-4-5-20251001"
    timeout_s: float = 15.0
    max_tokens: int = 512


class TranslatorConfig(BaseModel):
    provider: Literal["ollama", "claude", "fake"] = "ollama"
    source_lang: str = "en"
    target_lang: str = "ar"
    domain_prompt: str = "retail_furniture"
    context_turns: int = 6
    glossary_path: str | None = "glossary.yaml"  # relative to repo root; null disables
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    claude: ClaudeConfig = Field(default_factory=ClaudeConfig)


class KokoroTtsConfig(BaseModel):
    model_path: str = "models/kokoro-v1.0.onnx"
    voices_path: str = "models/voices-v1.0.bin"
    voice: str = "af_heart"
    lang: str = "en-us"  # Kokoro has no built-in Australian English voice; en-us/en-gb are closest
    speed: float = 1.0  # 1.0 is ~190 words/min with af_heart - brisk; 0.85 is conversational


class PiperTtsConfig(BaseModel):
    # piper-tts is GPL-3.0-or-later (not Apache/MIT) - see README "Licensing".
    model_path: str = "models/ar_JO-kareem-medium.onnx"
    config_path: str | None = None  # defaults to "<model_path>.json" if null
    length_scale: float | None = None  # >1 speaks slower; None = the voice's own default


class NeuralTtsConfig(BaseModel):
    """Microsoft's neural voices - the ones Edge's Read Aloud uses - through
    edge-tts: no account or key, needs internet. The local voices (kokoro,
    piper above) take over for any sentence they can't deliver."""

    voices: dict[str, str] = Field(default_factory=lambda: {"en": "en-US-AndrewNeural", "ar": "ar-JO-TaimNeural"})
    # Speaking rate per language, relative to the voice's own. At "+0%" they
    # measured ~200 words/min (English) and ~150 (Arabic) in the meeting
    # simulation - brisk for a call; these bring both to a conversational pace.
    rates: dict[str, str] = Field(default_factory=lambda: {"en": "-15%", "ar": "-10%"})
    timeout_s: float = 6.0


class TtsConfig(BaseModel):
    provider: Literal["none", "multi_voice", "neural", "fake"] = "none"
    sample_rate_hz: int = 24000
    kokoro: KokoroTtsConfig = Field(default_factory=KokoroTtsConfig)
    piper: PiperTtsConfig = Field(default_factory=PiperTtsConfig)
    neural: NeuralTtsConfig = Field(default_factory=NeuralTtsConfig)


class LoggingConfig(BaseModel):
    level: str = "INFO"
    structured: bool = True
    latency_metrics: bool = True


class EngineConfig(BaseModel):
    audio: AudioConfig = Field(default_factory=AudioConfig)
    vad: VadConfig = Field(default_factory=VadConfig)
    asr: AsrConfig = Field(default_factory=AsrConfig)
    translator: TranslatorConfig = Field(default_factory=TranslatorConfig)
    tts: TtsConfig = Field(default_factory=TtsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


def _default_config_path() -> Path:
    env_path = os.environ.get("ENGINE_CONFIG_PATH")
    if env_path:
        return Path(env_path).expanduser().resolve()
    cwd_candidate = Path.cwd() / "config.yaml"
    if cwd_candidate.is_file():
        return cwd_candidate
    return REPO_ROOT / "config.yaml"


@lru_cache(maxsize=1)
def load_config(path: Path | None = None) -> EngineConfig:
    """Load and cache the engine config. Call load_config.cache_clear() in tests."""
    load_dotenv(REPO_ROOT / ".env", override=False)
    config_path = path or _default_config_path()
    if not config_path.is_file():
        return EngineConfig()
    with config_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return EngineConfig.model_validate(raw)
