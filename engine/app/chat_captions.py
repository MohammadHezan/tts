"""Types the interpretation in the meeting's chat, live.

Every translation is posted the moment it exists - before its voice has even
been synthesized - so people who would rather read than listen (or who have the
voice switched off) follow the conversation a phrase behind the speaker. Lines
are tagged with the language they are written in: "[EN] what was said" then
"[AR] its translation". The chat only ever receives these lines - the bot
reads nothing from it and posts nothing else.

One queue per bot keeps the lines in order and keeps the voice and the
pipeline from ever waiting on the chat: the meeting service takes about a
second to post one message, so if people talk faster than that the waiting lines
go out together as one message instead of falling further behind. A failed
post is logged and dropped; it never touches the interpretation.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from app.attendee_client import AttendeeClient, AttendeeSettings
from app.logging_utils import get_logger, log_event

def caption_line(lang: str, text: str) -> str:
    return f"[{lang.upper()}] {text.strip()}"


def split_for_chat(text: str, limit: int) -> list[str]:
    """`text` as pieces of at most `limit` characters, cut between words."""
    pieces: list[str] = []
    current = ""
    for word in text.split():
        if current and len(current) + 1 + len(word) > limit:
            pieces.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        pieces.append(current)
    return pieces or [text]


def _default_client() -> AttendeeClient | None:
    settings = AttendeeSettings.from_env()
    return AttendeeClient(settings, timeout=10.0) if settings else None


class ChatCaptioner:
    def __init__(self, client_factory: Callable[[], AttendeeClient | None] | None = None, max_chars: int = 400) -> None:
        self._client_factory = client_factory or _default_client
        self._max_chars = max_chars
        self._queues: dict[str, asyncio.Queue[str | None]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def post(self, bot_id: str, line: str) -> None:
        """Queue one line for the bot's meeting chat; returns at once."""
        queue = self._queues.get(bot_id)
        if queue is None:
            queue = self._queues[bot_id] = asyncio.Queue()
            self._tasks[bot_id] = asyncio.create_task(self._run(bot_id, queue))
        queue.put_nowait(line)

    async def close(self, bot_id: str | None = None, timeout_s: float = 5.0) -> None:
        """Lets what is waiting go out (briefly), then stops the bot's poster."""
        for key in [bot_id] if bot_id else list(self._queues):
            queue, task = self._queues.pop(key, None), self._tasks.pop(key, None)
            if queue is None or task is None:
                continue
            queue.put_nowait(None)
            try:
                await asyncio.wait_for(task, timeout_s)
            except (asyncio.TimeoutError, Exception):
                task.cancel()

    async def _run(self, bot_id: str, queue: asyncio.Queue[str | None]) -> None:
        logger = get_logger()
        client = self._client_factory()
        if client is None:
            return
        try:
            finished = False
            while not finished:
                first = await queue.get()
                if first is None:
                    break
                lines = [first]
                while not queue.empty():  # the chat is behind: send what waits as one message
                    nxt = queue.get_nowait()
                    if nxt is None:
                        finished = True
                        break
                    lines.append(nxt)
                for piece in split_for_chat(" ".join(lines), self._max_chars):
                    try:
                        await client.send_chat_message(bot_id, piece)
                    except Exception as error:  # a chat hiccup must never affect the interpretation
                        log_event(logger, logging.WARNING, "chat_caption_failed", bot_id=bot_id, error=repr(error))
        finally:
            await client.aclose()
