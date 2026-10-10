"""Meeting notes and per-speaker summaries, written by a local model through
Ollama - in English, from the English text of the meeting (what was said in
English, and the interpreter's English translation of what was said in Arabic).

Long meetings go through in pieces: each piece is summarized, then the notes of
the pieces are combined (the model's window is small).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

import httpx

SYSTEM = (
    "You write meeting notes from a transcript. Use only what the transcript says; never invent facts, "
    "names, numbers or decisions. Write in English."
)
PERSON_PROMPT = (
    "Summarize what {name} said in this meeting in 3 to 5 short bullet points. Start every line with '- '. "
    "Only what {name} said or committed to. Write in the third person (for example '{name} will send the transfer on Thursday'), never 'I' or 'we'. Output only the bullet points.\n\nTranscript:\n{text}"
)
PART_PROMPT = (
    "These are the notes of one part of a longer {what}. Write 3 to 6 short bullet points that keep every "
    "fact, number, name, decision and action item. Start every line with '- '. Output only the bullet points.\n\n"
    "Text:\n{text}"
)
PERSON_COMBINE_PROMPT = (
    "These are notes on what {name} said in consecutive parts of one meeting. Write 3 to 5 short bullet points "
    "summarizing what {name} said or committed to in the whole meeting, in the third person (never 'I' or 'we'). Start every line with '- '. "
    "Output only the bullet points.\n\nNotes:\n{text}"
)
OVERALL_PROMPT = (
    "Write the notes for this meeting.\nFormat exactly:\nOverview: two or three sentences.\nKey points:\n- ...\n"
    "Decisions and action items:\n- who will do what, and by when if said (or 'None mentioned').\n\nTranscript:\n{text}"
)
COMBINE_PROMPT = (
    "These are notes from consecutive parts of one meeting. Write the notes for the whole meeting.\nFormat exactly:\n"
    "Overview: two or three sentences.\nKey points:\n- ...\nDecisions and action items:\n- who will do what, and by "
    "when if said (or 'None mentioned').\n\nNotes:\n{text}"
)

AskModel = Callable[[str], Awaitable[str]]


def ollama_asker(base_url: str, model: str, num_ctx: int | None, keep_alive: str, timeout_s: float = 300.0) -> AskModel:
    async def ask(prompt: str) -> str:
        options: dict = {"temperature": 0.2}
        if num_ctx:
            options["num_ctx"] = num_ctx
        async with httpx.AsyncClient(base_url=base_url, timeout=timeout_s) as client:
            response = await client.post("/api/chat", json={
                "model": model, "stream": False, "keep_alive": keep_alive, "options": options,
                "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
            })
            response.raise_for_status()
            return response.json()["message"]["content"].strip()

    return ask


def split_lines(lines: list[str], limit: int) -> list[str]:
    """The lines as pieces of at most `limit` characters (a line is never cut)."""
    pieces, current = [], ""
    for line in lines:
        if current and len(current) + 1 + len(line) > limit:
            pieces.append(current)
            current = line
        else:
            current = f"{current}\n{line}".strip("\n")
    if current:
        pieces.append(current)
    return pieces


async def _summarize(ask: AskModel, lines: list[str], first_prompt: str, what: str, combine_prompt: str, limit: int, **fields: str) -> str:
    pieces = split_lines(lines, limit)
    if len(pieces) <= 1:
        return await ask(first_prompt.format(text=pieces[0] if pieces else "", **fields))
    notes = [await ask(PART_PROMPT.format(what=what, text=piece)) for piece in pieces]
    # The notes of the parts may themselves be long: combine in rounds.
    while len(notes) > 1 and sum(map(len, notes)) > limit:
        merged = split_lines(notes, limit)
        notes = [await ask(PART_PROMPT.format(what=what, text=m)) for m in merged]
    return await ask(combine_prompt.format(text="\n\n".join(notes), **fields))


async def summarize_person(ask: AskModel, name: str, lines: list[str], limit: int = 6000) -> str:
    return await _summarize(ask, lines, PERSON_PROMPT, f"meeting (only what {name} said)", PERSON_COMBINE_PROMPT, limit, name=name)


async def summarize_meeting(ask: AskModel, lines: list[str], limit: int = 6000) -> str:
    return await _summarize(ask, lines, OVERALL_PROMPT, "meeting", COMBINE_PROMPT, limit)


# --- turning the model's text into document parts

_HEADINGS = ("overview", "key points", "decisions and action items")
_BULLET = re.compile(r"^\s*(?:[-*•–]|\d+[.)])\s+(.*)$")


def parse_notes(text: str) -> list[tuple[str, str]]:
    """[(kind, text)], kind: "heading" | "paragraph" | "bullet"."""
    parts: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = re.sub(r"^#+\s*", "", raw.strip().replace("**", "").replace("__", ""))
        if not line:
            continue
        head = re.match(r"^(" + "|".join(_HEADINGS) + r")\s*:?\s*(.*)$", line, re.IGNORECASE)
        if head and (not head.group(2) or head.group(1).lower() == "overview"):
            parts.append(("heading", head.group(1).capitalize()))
            if head.group(2):
                parts.append(("paragraph", head.group(2)))
        elif (bullet := _BULLET.match(line)):
            parts.append(("bullet", bullet.group(1).strip()))
        else:
            parts.append(("paragraph", line))
    return parts


def bullets_only(text: str) -> list[tuple[str, str]]:
    """A speaker's summary: the bullet points, without the model's lead-in sentence."""
    parts = parse_notes(text)
    bullets = [p for p in parts if p[0] == "bullet"]
    return bullets or parts
