"""Comparable form of a transcript: no Arabic diacritics/tatweel, punctuation or
case, single spaces. Used to spot Whisper's stock phrases and the bot's own
voice coming back (providers/asr_faster_whisper.py, attendee_bridge.py)."""

from __future__ import annotations

import re
import unicodedata

_ARABIC_MARKS = re.compile("[ً-ٰٟـ]")  # harakat, superscript alef, tatweel


def normalize_text(text: str) -> str:
    text = _ARABIC_MARKS.sub("", unicodedata.normalize("NFKC", text)).lower()
    text = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in text)
    return " ".join(text.split())
