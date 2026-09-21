"""Cloud translator provider backed by the Claude Messages API.

Requires ANTHROPIC_API_KEY in the environment/.env - never hardcoded (see
.env.example). Talks to the REST API directly via httpx, rather than pulling
in the full anthropic SDK, to keep the engine's dependency footprint small
and fully under our own async client/timeout lifecycle.
"""

from __future__ import annotations

import os

import httpx

from app.config import REPO_ROOT, TranslatorConfig
from app.glossary import Glossary
from app.prompts import build_context_messages, build_system_prompt
from app.providers.base import TranslatorProvider, TurnContext

_API_URL = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"


class ClaudeTranslator(TranslatorProvider):
    def __init__(self, cfg: TranslatorConfig) -> None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "translator.provider=claude requires ANTHROPIC_API_KEY to be set "
                "(copy .env.example to .env and fill it in)"
            )
        self._cfg = cfg
        self._glossary = Glossary.load(REPO_ROOT / cfg.glossary_path if cfg.glossary_path else None)
        self._client = httpx.AsyncClient(
            timeout=cfg.claude.timeout_s,
            headers={
                "x-api-key": api_key,
                "anthropic-version": _API_VERSION,
                "content-type": "application/json",
            },
        )

    async def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        context: list[TurnContext] | None = None,
    ) -> str:
        messages = build_context_messages(context)
        messages.append({"role": "user", "content": text})

        response = await self._client.post(
            _API_URL,
            json={
                "model": self._cfg.claude.model,
                "system": build_system_prompt(source_lang, target_lang, self._cfg.domain_prompt, self._glossary),
                "messages": messages,
                "max_tokens": self._cfg.claude.max_tokens,
                "temperature": 0.2,
            },
        )
        response.raise_for_status()
        payload = response.json()
        return "".join(block["text"] for block in payload["content"] if block["type"] == "text").strip()

    async def aclose(self) -> None:
        await self._client.aclose()
