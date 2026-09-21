"""Shared domain system prompt for the LLM translator providers.

Keeps prompt engineering in one place so the retail/furniture domain framing
and the "preserve names/numbers exactly" constraint stay consistent whether
the translator backend is Ollama (local) or Claude (cloud).
"""

from __future__ import annotations

from app.providers.base import TurnContext

_LANG_NAMES = {"en": "English", "ar": "Arabic"}

_DOMAIN_PROMPTS = {
    "retail_furniture": (
        "You are a professional simultaneous interpreter for a live conversation "
        "between a Jordanian luxury furniture retailer (marketing and retail "
        "operations) and an Australian business counterpart discussing marketing, "
        "retail campaigns, and logistics for high-end furniture. Translate fluently "
        "and naturally for spoken conversation, not word-for-word."
    ),
}


def build_system_prompt(source_lang: str, target_lang: str, domain_prompt: str) -> str:
    src = _LANG_NAMES.get(source_lang, source_lang)
    tgt = _LANG_NAMES.get(target_lang, target_lang)
    domain = _DOMAIN_PROMPTS.get(domain_prompt, _DOMAIN_PROMPTS["retail_furniture"])
    target_register = (
        "clear Modern Standard Arabic suitable for a business setting"
        if target_lang == "ar"
        else "natural business English"
    )
    return (
        f"{domain}\n\n"
        f"Translate the given {src} text into {tgt} ({target_register}).\n"
        "Rules:\n"
        "- Output ONLY the translation. No notes, no quotes, no explanations.\n"
        "- Preserve proper names, brand names, product codes, and numbers "
        "(prices, quantities, dates, measurements) EXACTLY as given - do not "
        "convert units or currencies.\n"
        "- Keep the tone professional and concise, matching spoken business dialogue."
    )


def build_context_messages(context: list[TurnContext] | None) -> list[dict[str, str]]:
    """Render prior turns as alternating user/assistant messages for LLM context.

    Full context-window wiring (config.translator.context_turns) and glossary
    injection land in Phase 2; the shape here is stable so callers can start
    threading turn history through without a later provider API change.
    """
    if not context:
        return []
    messages: list[dict[str, str]] = []
    for turn in context:
        messages.append({"role": "user", "content": turn.source_text})
        messages.append({"role": "assistant", "content": turn.translated_text})
    return messages
