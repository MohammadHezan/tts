"""Translator prompts: one system prompt for both directions, and the clean-up
that keeps the bot from saying things nobody said."""

from __future__ import annotations

from app.glossary import Glossary, GlossaryTerm
from app.prompts import build_messages, build_system_prompt, clean_translation
from app.providers.base import TurnContext

GLOSSARY = Glossary([GlossaryTerm(en="walnut", ar="خشب الجوز")])


def test_system_prompt_is_identical_in_both_directions() -> None:
    # The direction flips nearly every turn in a bilingual call; a changing
    # system prompt would discard Ollama's prompt cache each time.
    prompt = build_system_prompt("retail_furniture", GLOSSARY)
    assert '"walnut" = "خشب الجوز"' in prompt
    assert "Never reply" in prompt
    en_ar = build_messages("Hello.", "en", "ar", None)
    ar_en = build_messages("مرحبا.", "ar", "en", None)
    assert en_ar[-1]["content"] == "Translate from English into Arabic:\nHello."
    assert ar_en[-1]["content"] == "Translate from Arabic into English:\nمرحبا."


def test_context_turns_use_the_same_request_shape() -> None:
    context = [TurnContext("صباح الخير", "Good morning", source_lang="ar", target_lang="en")]
    messages = build_messages("Thank you.", "en", "ar", context)
    assert messages == [
        {"role": "user", "content": "Translate from Arabic into English:\nصباح الخير"},
        {"role": "assistant", "content": "Good morning"},
        {"role": "user", "content": "Translate from English into Arabic:\nThank you."},
    ]


def test_drops_a_follow_up_the_model_invented() -> None:
    # Real output from llama3.1 8B in the meeting simulation.
    source = "ممتاز، نستطيع تسليم الطلب خلال ثلاثة أسابيع."
    output = (
        "Great, we can deliver the order within three weeks. Would you like to discuss the logistics "
        "and shipping details, or would you prefer to receive a formal invoice for the order?"
    )
    assert clean_translation(source, output) == "Great, we can deliver the order within three weeks."


def test_keeps_a_faithful_two_sentence_translation() -> None:
    # One long Arabic sentence often becomes two English ones - that's fine.
    source = "وصلت الشحنة إلى المستودع صباح اليوم، وسنبدأ التوصيل إلى العملاء غداً"
    output = "The shipment reached the warehouse this morning. We will start delivering to customers tomorrow."
    assert clean_translation(source, output) == output


def test_keeps_a_question_that_was_asked() -> None:
    source = "هل يمكنكم التسليم قبل نهاية الشهر؟ نحتاجها للافتتاح."
    output = "Can you deliver before the end of the month? We need them for the opening."
    assert clean_translation(source, output) == output


def test_strips_labels_and_quotes() -> None:
    assert clean_translation("Hello.", 'Translation: "مرحبا."') == "مرحبا."
    assert clean_translation("Hello.", "«مرحبا.»") == "مرحبا."
