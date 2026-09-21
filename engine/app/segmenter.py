"""Rule-based sentence segmenter.

Splits a finished ASR transcript (one VAD-endpointed "turn") into sentence-level
chunks so the translator can start on the first sentence of a long monologue
without waiting for the rest - the "no waiting for full utterances" streaming
constraint applied at the paragraph level, since Whisper only exposes
whole-utterance decoding (see providers/asr_faster_whisper.py's local-agreement
partials for the sub-utterance streaming story during speech itself).

Deliberately a small guarded regex, not an NLP sentence-boundary model: Phase 1
only needs to avoid splitting on common abbreviations and decimal numbers/
currency ("$1,299.50"), which this handles well enough for retail/business
speech without pulling in a model dependency for a rarely-ambiguous task.
"""

from __future__ import annotations

import re

# Abbreviations that end in a period but should NOT be treated as a sentence end.
_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st",
    "vs", "etc", "e.g", "i.e", "approx", "no", "fig",
}

# A '.', '!', '?' or Arabic '؟' followed by whitespace and an uppercase Latin or
# Arabic letter is treated as a candidate sentence boundary. Abbreviations are
# repaired afterwards (see _ends_with_protected_abbreviation).
_SPLIT_RE = re.compile(r"(?<=[.!?؟])\s+(?=[A-Z؀-ۿ])")
_DECIMAL_RE = re.compile(r"\d\.\d")
_PLACEHOLDER = "\u0000"


def segment(text: str) -> list[str]:
    """Split `text` into sentence-level chunks, preserving punctuation and numbers."""
    text = text.strip()
    if not text:
        return []

    # Protect decimal numbers/currency (e.g. "3.5", "$1,299.00") from the splitter
    # by swapping their '.' for a placeholder that never appears in speech text.
    protected = _DECIMAL_RE.sub(lambda m: m.group(0).replace(".", _PLACEHOLDER), text)

    sentences: list[str] = []
    buffer = ""
    for chunk in _SPLIT_RE.split(protected):
        buffer = f"{buffer} {chunk}".strip() if buffer else chunk
        if _ends_with_protected_abbreviation(buffer):
            continue  # not really a sentence end; keep accumulating
        sentences.append(buffer)
        buffer = ""
    if buffer:
        sentences.append(buffer)

    return [s.replace(_PLACEHOLDER, ".").strip() for s in sentences if s.strip()]


def _ends_with_protected_abbreviation(sentence: str) -> bool:
    words = sentence.rstrip(".!?؟ ").split()
    if not words:
        return False
    return words[-1].lower() in _ABBREVIATIONS
