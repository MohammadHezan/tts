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

    def format_for_prompt(self, source_lang: str, target_lang: str) -> str:
        """Renders the glossary as a system-prompt block for this turn's
        translation direction, or "" if empty/the language pair isn't en<->ar.
        """
        if not self._terms:
            return ""
        pairs = {"en", "ar"}
        if {source_lang, target_lang} != pairs:
            return ""
        lines = [f'- "{t.en if source_lang == "en" else t.ar}" -> "{t.ar if target_lang == "ar" else t.en}"' for t in self._terms]
        return "Use these exact translations for the following terms whenever they appear:\n" + "\n".join(lines)
