"""Shared domain prompt and output clean-up for the LLM translator providers.

Keeps prompt engineering in one place so the retail/furniture domain framing
and the "preserve names/numbers exactly" constraint stay consistent whether
the translator backend is Ollama (local) or Claude (cloud).

The system prompt is deliberately the same text for both directions; each
user message names the direction instead. In a bilingual call the direction
flips nearly every turn, and a direction-specific system prompt would throw
away a local model's prompt cache each time - re-reading the whole prompt is
most of a CPU translation's latency.
"""

from __future__ import annotations

import re

from app.glossary import Glossary
from app.providers.base import TurnContext
from app.segmenter import segment as segment_sentences

_LANG_NAMES = {"en": "English", "ar": "Arabic"}

_DOMAIN_PROMPTS = {
    "retail_furniture": (
        "The conversation is a live business call between a Jordanian luxury "
        "furniture retailer (marketing and retail operations) and an Australian "
        "business counterpart, about marketing, retail campaigns, and logistics "
        "for high-end furniture."
    ),
}


def build_system_prompt(domain_prompt: str, glossary: Glossary | None = None) -> str:
    domain = _DOMAIN_PROMPTS.get(domain_prompt, _DOMAIN_PROMPTS["retail_furniture"])
    prompt = (
        "You are the translation step of a live interpreter: what you write is "
        "spoken aloud to the other side of the call as the speaker's own words.\n"
        f"{domain}\n\n"
        "Each message gives one thing a speaker said and the language to translate "
        "it into (English to Arabic, or Arabic to English).\n"
        "Rules:\n"
        "- Output ONLY the translation of what was said. Never reply to it, answer "
        "it, continue the conversation, ask a question, or add anything that was "
        "not said - you are not a participant in the call.\n"
        "- Translate all of it, fluently and naturally for spoken conversation, "
        "not word-for-word.\n"
        "- Long speech arrives phrase by phrase while the speaker is still "
        "talking, so a message can be part of a sentence. Translate just that "
        "part so it follows on from the previous one - never finish the "
        "sentence or guess what comes next.\n"
        "- Arabic: clear Modern Standard Arabic suitable for a business setting. "
        "English: natural business English with an Australian register.\n"
        "- Preserve proper names, brand names, product codes, and numbers "
        "(prices, quantities, dates, measurements) EXACTLY as given - do not "
        "convert units or currencies.\n"
        "- No notes, no quotes, no explanations."
    )
    glossary_block = glossary.format_for_prompt() if glossary else ""
    if glossary_block:
        prompt += f"\n\n{glossary_block}"
    return prompt


def build_translation_request(text: str, source_lang: str, target_lang: str) -> str:
    src = _LANG_NAMES.get(source_lang, source_lang)
    tgt = _LANG_NAMES.get(target_lang, target_lang)
    return f"Translate from {src} into {tgt}:\n{text}"


def build_messages(text: str, source_lang: str, target_lang: str, context: list[TurnContext] | None) -> list[dict[str, str]]:
    """Prior turns (Pipeline's rolling context_turns window) as the same
    request/translation pairs, then this turn's request."""
    messages: list[dict[str, str]] = []
    for turn in context or []:
        messages.append({"role": "user", "content": build_translation_request(turn.source_text, turn.source_lang, turn.target_lang)})
        messages.append({"role": "assistant", "content": turn.translated_text})
    messages.append({"role": "user", "content": build_translation_request(text, source_lang, target_lang)})
    return messages


def max_output_tokens(text: str) -> int:
    """Generous for any faithful translation, but stops a model that has started
    talking at length instead of translating."""
    return min(512, 48 + 3 * len(text))


_LABEL = re.compile(r"^\s*(translation|الترجمة)\s*[:：]\s*", re.IGNORECASE)
_QUOTES = "\"'“”«»"
# Faithful translations stay well under this length ratio (Arabic <-> English
# lengths are close); a reply the model tacked on is what pushes past it.
_MAX_LENGTH_RATIO = 2.2


def clean_translation(source: str, output: str) -> str:
    """Strip what a small model adds around a translation: a "Translation:"
    label, wrapping quotes, and - the one that matters in a live call - extra
    sentences nobody said (a follow-up question, an offer to help), which the
    bot would otherwise speak into the meeting as if the speaker had said them.
    """
    text = _LABEL.sub("", output.strip())
    if len(text) > 1 and text[0] in _QUOTES and text[-1] in _QUOTES:
        text = text[1:-1].strip()

    sentences = segment_sentences(text)
    if len(sentences) <= len(segment_sentences(source)):
        return text
    source_asks = "?" in source or "؟" in source
    limit = max(len(source) * _MAX_LENGTH_RATIO, 40)
    while len(sentences) > 1 and (
        len(" ".join(sentences)) > limit or (not source_asks and sentences[-1].rstrip().endswith(("?", "؟")))
    ):
        sentences.pop()
    return " ".join(sentences)
