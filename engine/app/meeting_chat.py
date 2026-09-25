"""Mute and unmute the interpreter from inside the meeting, through its chat.

Attendee's bot keeps its Meet/Teams/Zoom microphone off and switches it on
only to speak - so a host muting it in the meeting doesn't stick: the next
translation switches it back on, as any participant may unmute themselves.
The meeting chat is what everyone in the call can reach, on a phone or a
computer: typing "mute" (or "اسكت") there silences the bot, "unmute" (or
"تكلم") brings it back, and the bot confirms in the chat. Same switch as the
dashboard's and the app's mute button (BotControls).
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
HELLO = (
    "AI Interpreter is here. Type mute in this chat to silence it, unmute to hear it again. "
    "اكتب اسكت لكتم المترجم، و تكلم لإعادة صوته."
)
MUTED = "Interpreter muted - it keeps listening but stays silent. Type unmute to hear it again. تم كتم المترجم."
UNMUTED = "Interpreter unmuted - it speaks its translations again. عاد صوت المترجم."

POLL_S = 2.0
STATE_EVERY_POLLS = 5  # check whether the bot is still in the meeting every ~10s
IN_MEETING = {"joined_not_recording", "joined_recording", "joined_recording_paused", "joined_recording_permission_denied"}
FINISHED = {"ended", "fatal_error"}


def parse_command(text: str) -> bool | None:
    """True = mute, False = unmute, None = not a command (a whole-message match only)."""
    normalized = normalize_text(text)
    if normalized in MUTE_COMMANDS:
        return True
    if normalized in UNMUTE_COMMANDS:
        return False
    return None


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
                        if command is None or command == controls.is_muted(bot_id):
                            continue
                        controls.set_muted(bot_id, command)
                        log_event(
                            logger, logging.INFO, "attendee_bot_muted" if command else "attendee_bot_unmuted",
                            bot_id=bot_id, via="meeting_chat", by=message.get("sender_name"),
                        )
                        await announce(bot_id, MUTED if command else UNMUTED, client)
                    next_url = page.get("next")
                    cursor = parse_qs(urlparse(next_url).query).get("cursor", [None])[0] if next_url else None
                    if not cursor:
                        break
            except Exception as error:  # Attendee briefly unreachable - keep following
                log_event(logger, logging.WARNING, "meeting_chat_poll_failed", bot_id=bot_id, error=repr(error))
            await asyncio.sleep(poll_s)
    finally:
        await client.aclose()
