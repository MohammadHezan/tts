"""Automated meeting bot: launches a headless Chromium via Playwright, joins
a Zoom or Google Meet call by URL as its own participant, and runs a single
bidirectional translation pipeline against PulseAudio virtual devices wired
to that browser's audio - no human clicking through Zoom/Meet's device
settings, no virtual-cable setup to do by hand.

This is the same general pattern real "meeting bot" products (Recall.ai and
similar) build for themselves - browser automation + OS audio routing - not
an official Zoom or Google feature. See README.md's Call mode section for
why Zoom's own official bot path (Meeting SDK, raw audio, no browser needed)
is gated behind a 4-6 week external app review to join meetings you don't
host, and why Google Meet has no bot API at all - this sidesteps both by
just being a browser, the same way any human joins from a laptop.

Only one Pipeline is needed here (not two, unlike cli/translate_call.py):
this bot is a single additional participant in the meeting, so it just needs
to listen to the meeting's mixed audio and speak translations back into that
same meeting - Pipeline already auto-detects the spoken language and flips
translation direction per utterance (see app/pipeline.py), so one instance
handles both directions of a two-person conversation happening through it.

    Meeting's audio out -> BOT_SPEAKER virtual sink's monitor -> Pipeline
    Pipeline's TTS output -> BOT_MIC virtual sink -> its monitor is what
    Chromium (and so Zoom/Meet) reads as this bot's own microphone.

Linux only (PulseAudio/PipeWire-pulse null-sinks). Windows would need a
different virtual-audio mechanism (see README.md's Call mode section on
VB-CABLE) - not built here.

HONESTY NOTE: outbound network access to zoom.us/meet.google.com is blocked
by this environment's egress policy, so _join_zoom()/_join_meet() could not
be run against a real join page while writing this - they're written
defensively (accessible-role/placeholder text matching with generous
timeouts, not brittle hardcoded CSS selectors) but are unverified. Run with
--headed to watch the join attempt; if it stalls, that tells you exactly
which step (name field, join button) needs a different selector for
whatever Zoom/Meet's current page actually looks like.

Usage:
    pip install -r requirements-dev.txt   # adds playwright
    playwright install chromium            # skip if using --chromium-path
    python -m cli.meeting_bot --url "https://meet.google.com/xxx-xxxx-xxx" --name "AI Interpreter"
    python -m cli.meeting_bot --url "https://app.zoom.us/wc/1234567890/join" --name "AI Interpreter" --headed
"""

from __future__ import annotations

import argparse
import asyncio
import re
import subprocess
from pathlib import Path

import sounddevice as sd

from app.config import load_config
from app.logging_utils import configure_logging
from cli.pipeline_runner import run_direction

MIC_SINK_NAME = "interpreter_bot_mic"
SPEAKER_SINK_NAME = "interpreter_bot_speaker"


def _pactl(*args: str) -> str:
    result = subprocess.run(["pactl", *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _load_null_sink(sink_name: str, description: str) -> int:
    output = _pactl(
        "load-module",
        "module-null-sink",
        f"sink_name={sink_name}",
        f"sink_properties=device.description={description}",
    )
    return int(output)


def _unload_module(module_id: int) -> None:
    subprocess.run(["pactl", "unload-module", str(module_id)], check=False)


def _find_device_index(name_substring: str, *, want_input: bool) -> int:
    devices = sd.query_devices()
    for index, info in enumerate(devices):
        if name_substring.lower() not in info["name"].lower():
            continue
        channels = info["max_input_channels"] if want_input else info["max_output_channels"]
        if channels > 0:
            return index
    kind = "input" if want_input else "output"
    raise RuntimeError(
        f"No {kind} device matching {name_substring!r} found via PortAudio. "
        "Run `python -m cli.translate_mic --list-devices` to see what PortAudio "
        "actually sees, and confirm PulseAudio/PipeWire is running."
    )


def find_chromium(explicit_path: str | None) -> str:
    if explicit_path:
        return explicit_path
    default = Path("/opt/pw-browsers")
    if default.is_dir():
        candidates = sorted(default.glob("chromium-*/chrome-linux/chrome"))
        if candidates:
            return str(candidates[-1])
    raise RuntimeError("Chromium not found - pass --chromium-path or run `playwright install chromium`")


async def _fill_name_if_present(page, bot_name: str) -> None:
    # Best-effort: not every join flow asks for a name (already signed in,
    # or the meeting skips straight to a lobby). Swallowing failures here is
    # intentional - this field's presence/selector is exactly what couldn't
    # be verified against a live page (see module docstring).
    try:
        name_input = page.get_by_placeholder(re.compile("your name", re.I)).first
        await name_input.fill(bot_name, timeout=8000)
    except Exception:
        pass


async def _join_meet(page, url: str, bot_name: str) -> None:
    await page.goto(url, wait_until="domcontentloaded")
    await _fill_name_if_present(page, bot_name)
    join_button = page.get_by_role("button", name=re.compile("ask to join|join now", re.I)).first
    await join_button.click(timeout=20000)


async def _join_zoom(page, url: str, bot_name: str) -> None:
    await page.goto(url, wait_until="domcontentloaded")
    await _fill_name_if_present(page, bot_name)
    join_button = page.get_by_role("button", name=re.compile(r"^join$", re.I)).first
    await join_button.click(timeout=20000)


async def run(args: argparse.Namespace) -> None:
    cfg = load_config()
    if cfg.tts.provider == "none":
        cfg.tts.provider = "multi_voice"
    configure_logging(cfg.logging)

    from playwright.async_api import async_playwright

    chromium_path = find_chromium(args.chromium_path)

    original_sink = _pactl("get-default-sink")
    original_source = _pactl("get-default-source")
    mic_module_id = _load_null_sink(MIC_SINK_NAME, "Interpreter bot mic")
    speaker_module_id = _load_null_sink(SPEAKER_SINK_NAME, "Interpreter bot speaker")
    _pactl("set-default-sink", SPEAKER_SINK_NAME)
    _pactl("set-default-source", f"{MIC_SINK_NAME}.monitor")

    try:
        input_device = _find_device_index(f"{SPEAKER_SINK_NAME}.monitor", want_input=True)
        output_device = _find_device_index(MIC_SINK_NAME, want_input=False)

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                executable_path=chromium_path,
                headless=not args.headed,
                args=["--use-fake-ui-for-media-stream"],
            )
            context = await browser.new_context(permissions=["microphone", "camera"])
            page = await context.new_page()

            if "meet.google.com" in args.url:
                await _join_meet(page, args.url, args.name)
            elif "zoom.us" in args.url:
                await _join_zoom(page, args.url, args.name)
            else:
                raise ValueError("--url must be a meet.google.com or zoom.us link")

            print(f"Bot joined: {args.url}")
            print("Translating live - Ctrl+C to leave and stop.")

            try:
                await run_direction("BOT", cfg, input_device, output_device)
            finally:
                await browser.close()
    finally:
        _pactl("set-default-sink", original_sink)
        _pactl("set-default-source", original_source)
        _unload_module(speaker_module_id)
        _unload_module(mic_module_id)
        print("Restored the original default audio devices and removed the virtual sinks.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", required=True, help="The meet.google.com or zoom.us meeting link")
    parser.add_argument("--name", default="AI Interpreter", help="Display name the bot joins with")
    parser.add_argument("--headed", action="store_true", help="Show the browser window instead of headless (recommended the first time)")
    parser.add_argument("--chromium-path", default=None, help="Path to a Chromium/Chrome executable")
    args = parser.parse_args()

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
