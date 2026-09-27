"""Mute and unmute the interpreter from inside the meeting, through its chat.

Attendee's bot keeps its Meet/Teams/Zoom microphone off and switches it on
only to speak - so a host muting it in the meeting doesn't stick: the next
translation switches it back on, as any participant may unmute themselves.
The meeting chat is what everyone in the call can reach, on a phone or a
computer: typing "mute" (or "اسكت") there silences the bot, "unmute" (or
"تكلم") brings it back, and the bot confirms in the chat. Same switch as the
dashboard's and the app's mute button (BotControls).

The meeting mixes the bot's voice for everyone, so no one can mute it for
themselves alone. But each side only needs the translations into its own
language: "mute arabic" / "اسكت عربي" stops the Arabic voice (the English
side still hears theirs), "mute english" / "اسكت انجليزي" the English one.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from collections.abc import Callable
from urllib.parse import parse_qs, urlparse

from app.attendee_bridge import BotControls
from app.attendee_client import AttendeeClient, AttendeeSettings
from app.logging_utils import get_logger, log_event
from app.text_normalize import normalize_text

MUTE_COMMANDS = frozenset({"mute", "mute interpreter", "interpreter mute", "اسكت", "كتم", "كتم المترجم", "صامت"})
UNMUTE_COMMANDS = frozenset(
    {"unmute", "unmute interpreter", "interpreter unmute", "تكلم", "الغاء الكتم", "إلغاء الكتم"}
)
_LANGUAGE_WORDS = {
    "ar": {"arabic", "ar", "عربي", "العربي", "بالعربي", "العربية", "عربية"},
    "en": {"english", "en", "انجليزي", "الانجليزي", "إنجليزي", "الإنجليزي", "بالانجليزي", "بالإنجليزي",
           "انكليزي", "الانكليزي", "الإنجليزية", "الانجليزية"},
}
_MUTE_WORDS = {"mute", "off", "اسكت", "كتم", "بدون", "no", "stop", "silence"}
_UNMUTE_WORDS = {"unmute", "on", "تكلم", "رجع", "ارجع", "الغاء كتم", "إلغاء كتم", "resume"}
HELLO = "Interpreter here. Type: mute / unmute, mute arabic / mute english. اكتب: اسكت / تكلم، اسكت عربي / اسكت انجليزي"
MUTED = "Muted. تم الكتم"
UNMUTED = "Unmuted. عاد الصوت"
LANGUAGE_MUTED = {"ar": "Arabic off. تم إيقاف العربي", "en": "English off. تم إيقاف الإنجليزي"}
LANGUAGE_UNMUTED = {"ar": "Arabic on. عاد العربي", "en": "English on. عاد الإنجليزي"}

POLL_S = 2.0
STATE_EVERY_POLLS = 5  # check whether the bot is still in the meeting every ~10s
IN_MEETING = {"joined_not_recording", "joined_recording", "joined_recording_paused", "joined_recording_permission_denied"}
FINISHED = {"ended", "fatal_error"}


def parse_command(text: str) -> tuple[bool, str | None] | None:
    """(muted, language) - language None for the whole bot - or None if the
    message isn't a command (a whole-message match only)."""
    normalized = normalize_text(text)
    if normalized in MUTE_COMMANDS:
        return True, None
    if normalized in UNMUTE_COMMANDS:
        return False, None
    words = normalized.split()
    if not 2 <= len(words) <= 3:
        return None
    for lang, names in _LANGUAGE_WORDS.items():
        if words[-1] in names or words[0] in names:
            rest = " ".join(w for w in words if w not in names)
            if rest in _MUTE_WORDS:
                return True, lang
            if rest in _UNMUTE_WORDS:
                return False, lang
    return None


def apply_command(controls: BotControls, bot_id: str, command: tuple[bool, str | None]) -> str | None:
    """Flips the switch; the chat announcement, or None if it was already so."""
    muted, lang = command
    if lang is None:
        if controls.is_muted(bot_id) == muted:
            return None
        controls.set_muted(bot_id, muted)
        return MUTED if muted else UNMUTED
    if (lang in controls.muted_languages(bot_id)) == muted:
        return None
    controls.set_language_muted(bot_id, lang, muted)
    return LANGUAGE_MUTED[lang] if muted else LANGUAGE_UNMUTED[lang]


def _default_client() -> AttendeeClient | None:
    settings = AttendeeSettings.from_env()
    return AttendeeClient(settings, timeout=10.0) if settings else None


def _iso(moment: datetime.datetime) -> str:
    return moment.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


async def announce(bot_id: str, message: str, client: AttendeeClient | None = None) -> None:
    owned = client is None
    if client is None:
        settings = AttendeeSettings.from_env()
        if settings is None:
            return
        client = AttendeeClient(settings, timeout=10.0)
    try:
        await client.send_chat_message(bot_id, message)
    except Exception as error:  # a chat hiccup must never affect the interpretation
        log_event(get_logger(), logging.WARNING, "meeting_chat_send_failed", bot_id=bot_id, error=repr(error))
    finally:
        if owned:
            await client.aclose()


async def watch_chat(
    bot_id: str,
    controls: BotControls,
    client_factory: Callable[[], AttendeeClient | None] | None = None,
    poll_s: float = POLL_S,
) -> None:
    """Follows one bot's meeting chat until it leaves the meeting."""
    logger = get_logger()
    client = (client_factory or _default_client)()
    if client is None:
        return
    since = _iso(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=5))
    seen: set[str] = set()
    greeted = False
    polls = 0
    try:
        while True:
            if polls % STATE_EVERY_POLLS == 0:
                try:
                    state = (await client.get_bot(bot_id)).get("state")
                except Exception:  # a missed status check is retried on the next round
                    state = None
                if state in FINISHED:
                    return
                if state in IN_MEETING and not greeted:
                    greeted = True
                    await announce(bot_id, HELLO, client)
            polls += 1
            cursor = None
            try:
                while True:
                    page = await client.chat_messages(bot_id, since, cursor)
                    for message in page.get("results") or []:
                        if message.get("id") in seen:
                            continue
                        seen.add(message.get("id"))
                        command = parse_command(message.get("text") or "")
                        if command is None:
                            continue
                        reply = apply_command(controls, bot_id, command)
                        if reply is None:
                            continue
                        log_event(
                            logger, logging.INFO, "attendee_bot_muted" if command[0] else "attendee_bot_unmuted",
                            bot_id=bot_id, language=command[1] or "all", via="meeting_chat", by=message.get("sender_name"),
                        )
                        await announce(bot_id, reply, client)
                    next_url = page.get("next")
                    cursor = parse_qs(urlparse(next_url).query).get("cursor", [None])[0] if next_url else None
                    if not cursor:
                        break
            except Exception as error:  # Attendee briefly unreachable - keep following
                log_event(logger, logging.WARNING, "meeting_chat_poll_failed", bot_id=bot_id, error=repr(error))
            await asyncio.sleep(poll_s)
    finally:
        await client.aclose()
