"""FastAPI WebSocket server: PCM16 frames in, JSON PipelineEvents out.

    ws://host:port/ws  - binary WS messages are 16kHz mono PCM16 frames
                          (20-30ms each); the server streams back one JSON
                          text message per PipelineEvent (see app/schema.py).

Run with (from the engine/ directory):
    uvicorn app.server:app --host 0.0.0.0 --port 8000

One Pipeline (and its VAD/ASR session state) is created per connection, since
speech endpointing is inherently per-stream. The ASR and Translator provider
instances are created once at process startup and shared across connections.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from app.config import load_config
from app.logging_utils import configure_logging, get_logger, log_event
from app.pipeline import Pipeline
from app.providers.base import build_asr_provider, build_translator_provider

_cfg = load_config()
_logger = configure_logging(_cfg.logging)
_asr = build_asr_provider(_cfg.asr)
_translator = build_translator_provider(_cfg.translator)

app = FastAPI(title="Arabic<->English Speech Translation Engine")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    pipeline = Pipeline(_cfg, _asr, _translator)
    log_event(_logger, logging.INFO, "ws_connected")
    try:
        while True:
            pcm16 = await ws.receive_bytes()
            async for event in pipeline.process_frame(pcm16):
                await ws.send_text(event.model_dump_json())
    except WebSocketDisconnect:
        pass
    finally:
        try:
            async for event in pipeline.flush():
                await ws.send_text(event.model_dump_json())
        except RuntimeError:
            pass  # socket already closed while flushing the last utterance
        log_event(get_logger(), logging.INFO, "ws_disconnected")
