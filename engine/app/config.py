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
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# app/config.py -> app/ -> engine/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[2]


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


class AsrConfig(BaseModel):
    provider: Literal["faster_whisper", "fake"] = "faster_whisper"
    model: str = "large-v3-turbo"
    device: Literal["auto", "cuda", "cpu"] = "auto"
    compute_type: str = "auto"
    language: str = "auto"
    beam_size: int = 1
    local_agreement: LocalAgreementConfig = Field(default_factory=LocalAgreementConfig)


class OllamaConfig(BaseModel):
    base_url: str = "http://localhost:11434"
    model: str = "llama3.1:8b-instruct-q4_K_M"
    timeout_s: float = 15.0


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
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    claude: ClaudeConfig = Field(default_factory=ClaudeConfig)


class TtsConfig(BaseModel):
    provider: Literal["none", "kokoro"] = "none"


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
