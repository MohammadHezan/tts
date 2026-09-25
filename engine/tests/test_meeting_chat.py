"""Muting the interpreter from inside the meeting: "mute" / "unmute" in its chat."""

from __future__ import annotations

import json

import httpx

from app.attendee_bridge import BotControls
from app.attendee_client import AttendeeClient, AttendeeSettings
from app.meeting_chat import HELLO, MUTED, UNMUTED, parse_command, watch_chat

BOT_ID = "bot_chat"


def test_commands_are_whole_messages_in_either_language() -> None:
    assert parse_command("mute") is True
    assert parse_command("  /Mute! ") is True
    assert parse_command("اسكت") is True
    assert parse_command("unmute") is False
    assert parse_command("تكلم") is False
    assert parse_command("إلغاء الكتم") is False
    assert parse_command("please mute the music") is None
    assert parse_command(MUTED) is None  # the bot's own confirmation is not a command
    assert parse_command(UNMUTED) is None
    assert parse_command(HELLO) is None


async def test_chat_mutes_and_unmutes_the_bot_and_it_answers() -> None:
    chat = [
        {"id": "m1", "text": "Hello everyone", "sender_name": "Sarah"},
        {"id": "m2", "text": "mute", "sender_name": "Omar"},
        {"id": "m3", "text": "mute", "sender_name": "Sarah"},  # already muted: nothing to do
        {"id": "m4", "text": "تكلم", "sender_name": "Omar"},
    ]
    visible: list[dict] = []
    sent: list[str] = []
    muted_after: list[bool] = []
    controls = BotControls()
    state_checks = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == f"/api/v1/bots/{BOT_ID}":
            state_checks["n"] += 1
            return httpx.Response(200, json={"id": BOT_ID, "state": "joined_recording" if state_checks["n"] < 4 else "ended"})
        if path == f"/api/v1/bots/{BOT_ID}/chat_messages":
            assert "updated_after" in request.url.params
            if chat:
                visible.append(chat.pop(0))  # one new message per poll, like a real conversation
            return httpx.Response(200, json={"next": None, "previous": None, "results": list(visible)})
        if path == f"/api/v1/bots/{BOT_ID}/send_chat_message":
            sent.append(json.loads(request.content)["message"])
            muted_after.append(controls.is_muted(BOT_ID))
            return httpx.Response(200, json={})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    await watch_chat(
        BOT_ID,
        controls,
        client_factory=lambda: AttendeeClient(AttendeeSettings("http://attendee.test", "k"), transport=transport),
        poll_s=0.001,
    )
    assert sent == [HELLO, MUTED, UNMUTED]
    assert muted_after == [False, True, False]
    assert controls.is_muted(BOT_ID) is False
