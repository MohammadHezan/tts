"""Domain glossary: exact preferred translations for brand/product/retail terms.

Injected into the translator's system prompt (see app/prompts.py) so the LLM
stays consistent turn to turn instead of inventing ad-hoc phrasing for names
like brand or collection names, campaign hashtags, or retail jargon that a
generic translation would otherwise render inconsistently.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class GlossaryTerm:
    en: str
    ar: str


class Glossary:
    def __init__(self, terms: list[GlossaryTerm]) -> None:
        self._terms = terms

    @classmethod
    def load(cls, path: str | Path | None) -> Glossary:
        """Loads terms from a YAML file shaped like:
            terms:
              - en: "Chesterfield sofa"
                ar: "أريكة تشيسترفيلد"
        Returns an empty glossary (no-op) if `path` is None or the file doesn't exist.
        """
        if path is None:
            return cls([])
        p = Path(path)
        if not p.is_file():
            return cls([])
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        terms = [GlossaryTerm(en=t["en"], ar=t["ar"]) for t in raw.get("terms", [])]
        return cls(terms)

    def __len__(self) -> int:
        return len(self._terms)

    def format_for_prompt(self) -> str:
        """Renders the glossary as a system-prompt block covering both directions
        (the same text whichever way a turn is translated - see app/prompts.py),
        or "" if empty.
        """
        if not self._terms:
            return ""
        lines = [f'- "{t.en}" = "{t.ar}"' for t in self._terms]
        return (
            "Always use these exact translations, in either direction (English = Arabic):\n"
            + "\n".join(lines)
        )
