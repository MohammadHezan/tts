"""Structured (JSON-lines) logging and per-stage latency instrumentation.

Usage:
    logger = configure_logging(cfg.logging)
    tracker = LatencyTracker(turn_id)
    with tracker.stage("asr"):
        ...
    with tracker.stage("translate"):
        ...
    tracker.total_ms()  # end-to-end latency for the turn
    tracker.as_dict()   # {"asr": 412.3, "translate": 180.1, ...}
"""

from __future__ import annotations

import json
import logging
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from app.config import LoggingConfig

_LOGGER_NAME = "tts_engine"


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": round(record.created, 3),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "extra_fields", None)
        if extra:
            payload.update(extra)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(cfg: LoggingConfig) -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(cfg.level.upper())
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    if cfg.structured:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)


def log_event(logger: logging.Logger, level: int, msg: str, **fields: object) -> None:
    logger.log(level, msg, extra={"extra_fields": fields})


@dataclass(frozen=True)
class StageLatency:
    stage: str
    turn_id: str
    duration_ms: float


@dataclass
class LatencyTracker:
    """Accumulates per-stage timings for one conversational turn."""

    turn_id: str
    _records: list[StageLatency] = field(default_factory=list)

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            self.record(name, (time.perf_counter() - start) * 1000)

    def record(self, name: str, duration_ms: float) -> None:
        """Record a stage timing measured elsewhere (e.g. only when it did real
        work, as opposed to a cheap gate-check no-op) - see Pipeline._maybe_partial.
        """
        self._records.append(StageLatency(stage=name, turn_id=self.turn_id, duration_ms=duration_ms))
        log_event(
            get_logger(),
            logging.INFO,
            "stage_complete",
            stage=name,
            turn_id=self.turn_id,
            latency_ms=round(duration_ms, 2),
        )

    @property
    def records(self) -> list[StageLatency]:
        return list(self._records)

    def total_ms(self) -> float:
        return sum(r.duration_ms for r in self._records)

    def as_dict(self) -> dict[str, float]:
        return {r.stage: round(r.duration_ms, 2) for r in self._records}
