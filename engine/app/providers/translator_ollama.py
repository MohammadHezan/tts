"""Local translator provider backed by an Ollama server (default; no API key).

Ollama must be running locally with the configured model pulled, e.g.:
    ollama pull llama3.1:8b-instruct-q4_K_M
"""

from __future__ import annotations

import httpx

from app.config import REPO_ROOT, TranslatorConfig
from app.glossary import Glossary
from app.prompts import build_messages, build_system_prompt, clean_translation, max_output_tokens
from app.providers.base import TranslatorProvider, TurnContext


class OllamaTranslator(TranslatorProvider):
    def __init__(self, cfg: TranslatorConfig) -> None:
        self._cfg = cfg
        self._glossary = Glossary.load(REPO_ROOT / cfg.glossary_path if cfg.glossary_path else None)
        self._client = httpx.AsyncClient(base_url=cfg.ollama.base_url, timeout=cfg.ollama.timeout_s)

    async def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        context: list[TurnContext] | None = None,
    ) -> str:
        system_prompt = build_system_prompt(self._cfg.domain_prompt, self._glossary)
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(build_messages(text, source_lang, target_lang, context))

        response = await self._client.post(
            "/api/chat",
            json={
                "model": self._cfg.ollama.model,
                "messages": messages,
                "stream": False,
                "keep_alive": self._cfg.ollama.keep_alive,
                # Deterministic: a live interpreter should say the same thing
                # for the same sentence, not a creative variation of it.
                "options": {"temperature": 0, "num_predict": max_output_tokens(text)},
            },
        )
        response.raise_for_status()
        payload = response.json()
        return clean_translation(text, str(payload["message"]["content"]))

    async def aclose(self) -> None:
        await self._client.aclose()
