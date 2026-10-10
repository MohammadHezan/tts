"""Puts the meeting summary together: the record on disk, who spoke when from
Attendee, the notes from the local model, and the Word document."""

from __future__ import annotations

import datetime
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.attendee_client import AttendeeClient
from app.logging_utils import get_logger, log_event
from app.meeting_docx import build_docx
from app.meeting_record import MeetingRecorder, attribute_speakers
from app.summary import AskModel, summarize_meeting, summarize_person

MAX_EVENT_PAGES = 400


class NothingToSummarize(Exception):
    pass


async def fetch_participant_events(client: AttendeeClient, bot_id: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    cursor = None
    for _ in range(MAX_EVENT_PAGES):
        page = await client.participant_events(bot_id, cursor)
        events += page.get("results") or []
        next_url = page.get("next")
        cursor = parse_qs(urlparse(next_url).query).get("cursor", [None])[0] if next_url else None
        if not cursor:
            break
    return events


async def make_summary(
    recorder: MeetingRecorder,
    bot_id: str,
    get_events: Callable[[], Awaitable[list[dict[str, Any]]]],
    ask: AskModel,
    tz: datetime.timezone = datetime.timezone.utc,
    with_summary: bool = True,
) -> bytes:
    phrases = recorder.load(bot_id)
    if not phrases:
        raise NothingToSummarize
    try:
        events = await get_events()
    except Exception as error:  # without speaker data the summary still works, under "Unknown speaker"
        log_event(get_logger(), logging.WARNING, "summary_speakers_unavailable", bot_id=bot_id, error=repr(error))
        events = []
    speakers = attribute_speakers(phrases, events)
    spoken = [f"{p.speaker}: {p.english}" for p in phrases if p.english]
    overall = ""
    per_speaker: dict[str, str] = {}
    if with_summary:
        overall = await summarize_meeting(ask, spoken) if spoken else "Overview: Nothing that could be summarized was said."
    for name in speakers if with_summary else []:
        own = [f"{name}: {p.english}" for p in phrases if p.speaker == name and p.english]
        per_speaker[name] = await summarize_person(ask, name, own) if own else ""
    return build_docx(phrases, speakers, overall, per_speaker, tz, with_summary)
