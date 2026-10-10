"""The meeting record behind the summary document: every phrase heard and its
translation, with times, appended to a file on this PC as the meeting goes
(meeting.record_dir). Who said each phrase comes later, from Attendee's own
record of who was speaking when (speech start / stop of each participant) -
the audio we get is one mixed stream, so it can't tell by itself.

File: one JSON object per line.
  {"kind":"heard","turn":..,"lang":"ar","text":"..","start_ms":..,"end_ms":..}
  {"kind":"translated","turn":..,"lang":"en","text":"..","merged":[earlier turns translated with it]}
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.logging_utils import get_logger, log_event
from app.schema import EventType, PipelineEvent

WINDOW_MS = 400  # a speaking indicator can lead or lag the words by this much
NEAREST_MS = 2500  # no overlap: the nearest speaker within this much
STOP_MISSING_MS = 5000  # a speech start without a stop counts for this long


@dataclass
class Phrase:
    turn_id: str
    lang: str
    source: str  # as heard
    start_ms: int
    end_ms: int
    english: str = ""  # itself if English, else its translation (the last phrase of a merged batch carries the batch's)
    speaker: str = "Unknown speaker"


class MeetingRecorder:
    def __init__(self, directory: str | Path | None) -> None:
        self._dir = Path(directory) if directory else None

    @property
    def enabled(self) -> bool:
        return self._dir is not None

    def path(self, bot_id: str) -> Path:
        assert self._dir is not None
        return self._dir / (re.sub(r"[^A-Za-z0-9_-]", "_", bot_id) + ".jsonl")

    def summary_path(self, bot_id: str) -> Path:
        return self.path(bot_id).with_name(self.path(bot_id).stem + "-summary.docx")

    def record(self, bot_id: str, event: PipelineEvent) -> None:
        if self._dir is None or not event.text:
            return
        if event.type is EventType.FINAL:
            end = event.timestamp * 1000 - (event.latency_ms or 0.0)  # the speaker stopped this long before the event
            line = {"kind": "heard", "turn": event.turn_id, "lang": event.lang, "text": event.text,
                    "start_ms": int(end - (event.speech_ms or 0.0)), "end_ms": int(end)}
        elif event.type is EventType.TRANSLATION:
            line = {"kind": "translated", "turn": event.turn_id, "lang": event.lang, "text": event.text, "merged": event.merged_turn_ids}
        else:
            return
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            with self.path(bot_id).open("a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        except OSError as error:  # a full disk must never touch the interpretation
            log_event(get_logger(), logging.WARNING, "meeting_record_failed", bot_id=bot_id, error=repr(error))

    def load(self, bot_id: str) -> list[Phrase]:
        if self._dir is None or not self.path(bot_id).exists():
            return []
        return phrases_from_lines(self.path(bot_id).read_text(encoding="utf-8").splitlines())


def phrases_from_lines(lines: list[str]) -> list[Phrase]:
    phrases: dict[str, Phrase] = {}
    for raw in lines:
        try:
            d = json.loads(raw)
        except ValueError:
            continue  # a half-written last line
        if d.get("kind") == "heard":
            phrases[d["turn"]] = Phrase(d["turn"], d["lang"], d["text"], d["start_ms"], d["end_ms"], english=d["text"] if d["lang"] == "en" else "")
        elif d.get("kind") == "translated" and d.get("lang") == "en":
            # into English: the English of an Arabic phrase (and of the ones queued behind it, translated together)
            phrase = phrases.get(d["turn"])
            if phrase is not None and phrase.lang != "en":
                phrase.english = f"{phrase.english} {d['text']}".strip()
    return sorted(phrases.values(), key=lambda p: p.end_ms)


def speaking_intervals(events: list[dict[str, Any]]) -> tuple[dict[str, list[tuple[int, int]]], dict[str, str]]:
    """({participant: [(start_ms, stop_ms)]}, {participant: name}) from Attendee's participant events."""
    intervals: dict[str, list[tuple[int, int]]] = defaultdict(list)
    names: dict[str, str] = {}
    opened: dict[str, int] = {}
    for event in sorted(events, key=lambda e: e.get("timestamp_ms", 0)):
        who, kind, at = event.get("participant_uuid"), event.get("event_type"), int(event.get("timestamp_ms", 0))
        if not who:
            continue
        if event.get("participant_name"):
            names[who] = event["participant_name"]
        if kind == "speech_start":
            opened[who] = at
        elif kind == "speech_stop" and who in opened:
            intervals[who].append((opened.pop(who), at))
    for who, start in opened.items():
        intervals[who].append((start, start + STOP_MISSING_MS))
    return intervals, names


def attribute_speakers(phrases: list[Phrase], events: list[dict[str, Any]]) -> list[str]:
    """Sets each phrase's speaker; returns the speakers in order of first speech."""
    intervals, names = speaking_intervals(events)
    labels: dict[str, str] = {}
    used: dict[str, int] = defaultdict(int)

    def label(who: str) -> str:
        if who not in labels:
            base = names.get(who) or f"Speaker {len(labels) + 1}"
            used[base] += 1
            labels[who] = base if used[base] == 1 else f"{base} ({used[base]})"
        return labels[who]

    order: list[str] = []
    for phrase in phrases:
        lo, hi = phrase.start_ms - WINDOW_MS, phrase.end_ms + WINDOW_MS
        overlap = {who: sum(max(0, min(hi, b) - max(lo, a)) for a, b in spans) for who, spans in intervals.items()}
        best = max(overlap, key=overlap.get, default=None)
        if best is None or overlap[best] <= 0:
            gaps = {who: min(max(a - phrase.end_ms, phrase.start_ms - b, 0) for a, b in spans) for who, spans in intervals.items()}
            best = min(gaps, key=gaps.get, default=None)
            if best is None or gaps[best] > NEAREST_MS:
                best = None
        phrase.speaker = label(best) if best else "Unknown speaker"
        if phrase.speaker not in order:
            order.append(phrase.speaker)
    return order
