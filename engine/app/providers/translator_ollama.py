"""Local translator provider backed by an Ollama server (default; no API key).

Ollama must be running locally with the configured model pulled, e.g.:
    ollama pull llama3.1:8b-instruct-q4_K_M
"""

from __future__ import annotations

import httpx

from app.config import TranslatorConfig
from app.prompts import build_context_messages, build_system_prompt
from app.providers.base import TranslatorProvider, TurnContext


class OllamaTranslator(TranslatorProvider):
    def __init__(self, cfg: TranslatorConfig) -> None:
        self._cfg = cfg
        self._client = httpx.AsyncClient(base_url=cfg.ollama.base_url, timeout=cfg.ollama.timeout_s)

    async def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        context: list[TurnContext] | None = None,
    ) -> str:
        messages = [{"role": "system", "content": build_system_prompt(source_lang, target_lang, self._cfg.domain_prompt)}]
        messages.extend(build_context_messages(context))
        messages.append({"role": "user", "content": text})

        response = await self._client.post(
            "/api/chat",
            json={
                "model": self._cfg.ollama.model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": 0.2},
            },
        )
        response.raise_for_status()
        payload = response.json()
        return str(payload["message"]["content"]).strip()

    async def aclose(self) -> None:
        await self._client.aclose()
