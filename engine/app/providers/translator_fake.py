"""Deterministic fake translator - no network, no API key. Used by unit tests
and the CLI harness's --dry-run mode.
"""

from __future__ import annotations

from app.providers.base import TranslatorProvider, TurnContext


class FakeTranslator(TranslatorProvider):
    def __init__(self, prefix: str = "[AR]") -> None:
        self._prefix = prefix
        self.calls: list[str] = []

    async def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        context: list[TurnContext] | None = None,
    ) -> str:
        self.calls.append(text)
        return f"{self._prefix} {text}"
