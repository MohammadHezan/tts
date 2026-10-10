"""Checks for the dashboard switches (voice/TTS, chat text, pause) and the live
chat captions, against a fake Attendee. No models, no network:

    python test_meeting_controls.py
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import time

import httpx

sys.path.insert(0, ".")

from fastapi import WebSocketDisconnect  # noqa: E402

from app.attendee_bridge import BotControls, BotEventHub, run_bridge  # noqa: E402
from app.attendee_client import AttendeeClient, AttendeeSettings  # noqa: E402
from app.chat_captions import ChatCaptioner, caption_line, split_for_chat  # noqa: E402
from app.schema import EventType, PipelineEvent  # noqa: E402
from app.switches import apply_switch, is_valid, state  # noqa: E402

FAILED: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"  {detail}"))
    if not ok:
        FAILED.append(name)


def fake_attendee(sent: list[str], calls: list[str] | None = None, delay_s: float = 0.0):
    """An Attendee that records what the bot types (and every call made to it)."""

    async def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(f"{request.method} {request.url.path}")
        if request.url.path.endswith("/send_chat_message"):
            if delay_s:
                await asyncio.sleep(delay_s)
            sent.append(json.loads(request.content)["message"])
        return httpx.Response(200, json={})

    return AttendeeClient(AttendeeSettings("http://attendee.test", "key"), transport=httpx.MockTransport(handler))


def switches() -> None:
    c = BotControls()
    check("defaults: voice on, text on, not paused", not c.is_muted("b") and c.types_text("b", "ar") and not c.is_paused("b"))
    apply_switch(c, "b", "voice", "off")
    check("tts off: silent for both languages", c.is_muted("b") and not c.speaks("b", "ar") and not c.speaks("b", "en"))
    apply_switch(c, "b", "voice", "on")
    check("tts on", c.speaks("b", "ar") and c.speaks("b", "en"))
    apply_switch(c, "b", "voice", "off", "ar")
    check("tts arabic off only", c.speaks("b", "en") and not c.speaks("b", "ar") and state(c, "b")["muted_languages"] == ["ar"])
    apply_switch(c, "b", "text", "off")
    check("text off", not c.types_text("b", "ar") and not c.types_text("b", "en"))
    apply_switch(c, "b", "text", "both")
    check("text both", c.text_mode("b") == "both" and c.types_text("b", "ar"))
    apply_switch(c, "b", "text", "off", "en")
    check("english text off only", c.types_text("b", "ar") and not c.types_text("b", "en"))
    apply_switch(c, "b", "interpreter", "pause")
    check("pause", c.is_paused("b") and state(c, "b")["paused"])
    apply_switch(c, "b", "interpreter", "resume")
    check("resume", not c.is_paused("b"))
    check("other bots are untouched", not c.is_muted("other") and not c.is_paused("other") and c.types_text("other", "en"))
    d = BotControls(voice_default=False, text_default="off")
    check("config defaults: voice off, text off", d.is_muted("x") and not d.speaks("x", "en") and not d.types_text("x", "en"))
    apply_switch(d, "x", "voice", "on")
    check("voice can be switched on over a voice-off default", d.speaks("x", "en") and d.is_muted("y"))
    check("validation", is_valid("voice", "off", "ar") and not is_valid("voice", "pause", None)
          and not is_valid("text", "both", "ar") and not is_valid("interpreter", "pause", "ar") and not is_valid("nope", "on", None))


async def captions() -> None:
    check("tag", caption_line("ar", " مرحبا ") == "[AR] مرحبا" and caption_line("en", "hi") == "[EN] hi")
    check("split", split_for_chat("a b c d e f", 5) == ["a b c", "d e f"] and split_for_chat("", 5) == [""])

    sent: list[str] = []
    calls: list[str] = []
    cap = ChatCaptioner(lambda: fake_attendee(sent, calls), max_chars=400)
    cap.post("bot", "[EN] hello there")
    await asyncio.sleep(0.05)
    cap.post("bot", "[AR] مرحبا")
    await cap.close("bot")
    check("original then translation, in order", sent == ["[EN] hello there", "[AR] مرحبا"], sent)
    check("the bot only ever posts - it never reads the chat", set(calls) == {"POST /api/v1/bots/bot/send_chat_message"}, calls)

    slow: list[str] = []
    cap = ChatCaptioner(lambda: fake_attendee(slow, delay_s=0.2), max_chars=400)
    cap.post("bot", "[EN] line 0")
    await asyncio.sleep(0.05)  # line 0 is being posted; the rest pile up behind it
    for i in range(1, 5):
        cap.post("bot", f"[EN] line {i}")
    await cap.close("bot", timeout_s=3)
    check("a slow chat merges the backlog", slow[0] == "[EN] line 0" and len(slow) == 2 and " ".join(slow).count("line") == 5, slow)

    cap = ChatCaptioner(lambda: None)
    cap.post("bot", "x")
    await cap.close("bot")
    check("no Attendee configured: nothing breaks", True)


class FakePipeline:
    """Stands in for the real Pipeline: nobody needs to be heard, the test says when someone is talking."""

    def __init__(self) -> None:
        self.talking = False
        self.events: asyncio.Queue = asyncio.Queue()

    def speaker_active(self) -> bool:
        return self.talking

    async def process_frame(self, frame):
        return
        yield

    async def flush(self):
        return
        yield

    async def background_events(self):
        while (event := await self.events.get()) is not None:
            yield event

    def close(self) -> None:
        pass


class FakeSocket:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.sent: list[float] = []

    async def receive_text(self) -> str:
        item = await self.incoming.get()
        if item is None:
            raise WebSocketDisconnect()
        return item

    async def send_text(self, message: str) -> None:
        self.sent.append(time.monotonic())


def voice_event(chunks: int = 1) -> PipelineEvent:
    return PipelineEvent(type=EventType.AUDIO, lang="ar", text="x", turn_id="t", seq=1, latency_ms=0.0,
                         audio=bytes(2400 * 2 * chunks), audio_sample_rate=24000)


async def hold_scenario(hold: bool, talking: bool, stop_after: float | None, hold_max_s: float = 30.0, chunks: int = 1,
                        talk_again_after: float | None = None) -> list[float]:
    """Seconds after the voice is ready at which each chunk of it was sent."""
    pipe, ws, controls = FakePipeline(), FakeSocket(), BotControls(hold_default=hold)
    pipe.talking = talking
    task = asyncio.create_task(run_bridge(ws, lambda **hooks: pipe, 16000, 960, BotEventHub(), controls,
                                          half_duplex=False, hold_release_s=0.2, hold_max_s=hold_max_s))
    await ws.incoming.put(json.dumps({"bot_id": "b", "trigger": "realtime_audio.mixed",
                                      "data": {"chunk": base64.b64encode(bytes(1920)).decode(), "sample_rate": 16000}}))
    await asyncio.sleep(0.1)
    ready = time.monotonic()
    await pipe.events.put(voice_event(chunks))
    if stop_after is not None:
        await asyncio.sleep(stop_after)
        pipe.talking = False
    if talk_again_after is not None:
        await asyncio.sleep(talk_again_after)
        pipe.talking = True
    await asyncio.sleep(1.6)
    await pipe.events.put(None)
    await ws.incoming.put(None)
    await asyncio.wait_for(task, 5)
    return [round(t - ready, 2) for t in ws.sent]


async def holding() -> None:
    sent = await hold_scenario(hold=True, talking=True, stop_after=0.6)
    check("hold: nothing is spoken while someone talks", bool(sent) and sent[0] >= 0.6, sent)
    check("hold: speaks soon after they stop", bool(sent) and sent[0] < 1.1, sent)
    sent = await hold_scenario(hold=False, talking=True, stop_after=None)
    check("hold off: speaks as soon as it is ready, even while someone talks", bool(sent) and sent[0] < 0.3, sent)
    sent = await hold_scenario(hold=True, talking=False, stop_after=None)
    check("hold: nobody talking -> speaks after the short quiet only", bool(sent) and sent[0] < 0.6, sent)
    sent = await hold_scenario(hold=True, talking=True, stop_after=None, hold_max_s=0.6)
    check("hold: a long monologue is interpreted anyway after the cap", bool(sent) and 0.55 <= sent[0] < 1.1, sent)
    sent = await hold_scenario(hold=True, talking=False, stop_after=None, chunks=6, talk_again_after=0.45)
    check("hold: a clip that already started is not cut off", len(sent) == 6, sent)


async def main() -> int:
    switches()
    await captions()
    await holding()
    print(f"\n{'ALL PASSED' if not FAILED else 'FAILED: ' + ', '.join(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
