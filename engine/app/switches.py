"""The interpreter's per-bot switches, as the dashboard (and the phone app)
flip them mid-meeting:

    voice        speak the translations (TTS). Off: the bot stays silent and no
                 voice is even synthesized. A language can be switched off on
                 its own - the meeting mixes the bot's voice for everyone, so
                 each side turns off the language it doesn't need.
    text         type in the meeting chat: "off", "on" (translations) or
                 "both" (what was heard, then its translation). Also per language.
    hold         the voice is made while someone is talking but only spoken once
                 they stop (on), or spoken as soon as it is ready (off).
    interpreter  pause / resume: while paused the bot hears nothing, translates
                 nothing, says nothing - for a private aside.

Nothing here is reachable from the meeting itself: the meeting chat only ever
receives the interpretation (app/chat_captions.py).
"""

from __future__ import annotations

from app.attendee_bridge import BotControls

ALLOWED_VALUES = {"voice": {"on", "off"}, "hold": {"on", "off"}, "text": {"on", "off", "both"}, "interpreter": {"pause", "resume"}}


def is_valid(feature: str, value: str, language: str | None) -> bool:
    if value not in ALLOWED_VALUES.get(feature, ()):
        return False
    return not (language and (feature in ("interpreter", "hold") or value == "both"))


def apply_switch(controls: BotControls, bot_id: str, feature: str, value: str, language: str | None = None) -> None:
    """Sets one switch (see is_valid for what each takes)."""
    if feature == "voice":
        if language is None:
            controls.set_muted(bot_id, value == "off")
        else:
            controls.set_language_muted(bot_id, language, value == "off")
    elif feature == "hold":
        controls.set_hold(bot_id, value == "on")
    elif feature == "text":
        if language is not None:
            controls.set_text_language_off(bot_id, language, value == "off")
        else:
            controls.set_text_mode(bot_id, "off" if value == "off" else "both" if value == "both" else "translation")
    elif feature == "interpreter":
        controls.set_paused(bot_id, value == "pause")
    else:
        raise ValueError(f"no such switch: {feature}")


def state(controls: BotControls, bot_id: str) -> dict:
    return {
        "muted": controls.is_muted(bot_id),
        "muted_languages": sorted(controls.muted_languages(bot_id)),
        "text_mode": controls.text_mode(bot_id),
        "text_off_languages": sorted(controls.text_off_languages(bot_id)),
        "paused": controls.is_paused(bot_id),
        "hold": controls.holds_voice(bot_id),
    }
