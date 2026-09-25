"""Orchestrates VAD -> ASR (local-agreement streaming) -> segmenter -> translator
-> TTS into a transport-agnostic stream of PipelineEvents. Bidirectional: ASR
auto-detects the spoken language and translation/TTS direction follows
automatically (see the source_lang/target_lang resolution below). A rolling
window of prior turns (config.translator.context_turns) is fed back to the
translator for cross-turn consistency.

Used identically by the WebSocket server (server.py) and the CLI harnesses
(cli/translate_wav.py, cli/translate_mic.py): none of them know about VAD, ASR,
translator or TTS internals, they just push PCM16 frames in and read events out.

background=True (the meeting bot): process_frame only listens and transcribes;
translating and speaking happen in a queue behind it, in the order things
were said, and come out of background_events(). So the next phrase is heard
and transcribed while the last one is still being translated and spoken.
Phrases that pile up in the queue from the same speaker go to the translator
together - fewer, better-connected translations when the machine falls behind.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

import numpy as np

from app.config import EngineConfig
from app.logging_utils import LatencyTracker, get_logger, log_event
from app.providers.base import AsrHypothesis, AsrProvider, TranslatorProvider, TtsProvider, TurnContext
from app.segmenter import segment as segment_sentences
from app.schema import EventType, PipelineEvent, new_turn_id
from app.vad import SpeechEndpointer, VadEvent, VadEventType
from app.voicing import voiced_ms

_LOGGER = get_logger()
# Queued phrases merged into one translation stay under this many characters.
MAX_BATCH_CHARS = 400


@dataclass
class _Turn:
    """One utterance (or phrase) from speech start to its last event."""

    turn_id: str
    tracker: LatencyTracker
    speech_end_perf: float | None = None
    seq: int = 0
    merged_ids: list[str] = field(default_factory=list)  # earlier queued phrases translated with this one


def rms_dbfs(pcm16: np.ndarray) -> float:
    """Loudness of 16-bit audio: 0 dBFS is full scale, silence is about -96."""
    if len(pcm16) == 0:
        return -96.0
    rms = float(np.sqrt(np.mean(np.square(pcm16.astype(np.float64)))))
    return 20 * np.log10(max(rms, 1.0) / 32768.0)


class Pipeline:
    def __init__(
        self,
        cfg: EngineConfig,
        asr: AsrProvider,
        translator: TranslatorProvider,
        tts: TtsProvider | None = None,
        emit_partials: bool = True,
        accept_transcript: Callable[[str, str], bool] | None = None,
        should_speak: Callable[[], bool] | None = None,
        background: bool = False,
    ) -> None:
        self._cfg = cfg
        # accept_transcript(text, lang): False drops the utterance as if it were
        # silence (the meeting bridge uses it to ignore its own voice coming
        # back). should_speak(): False skips TTS for now (the bot is muted).
        self._accept_transcript = accept_transcript
        self._should_speak = should_speak
        # Partials only feed live captions. A meeting bot never shows them, and
        # on CPU each re-decode steals the time the final transcript needs.
        self._emit_partials = emit_partials
        self._asr = asr
        self._translator = translator
        self._tts = tts
        self._vad = SpeechEndpointer(cfg=cfg.vad, sample_rate=cfg.audio.sample_rate_hz)
        self._turn: _Turn | None = None  # the utterance being heard right now
        self._latency_by_turn: dict[str, dict[str, float]] = {}
        self._partial_index = 0
        # Rolling context window of prior turns, fed back to the translator for
        # consistency (pronoun resolution, terminology) across the conversation.
        self._context: deque[TurnContext] = deque(maxlen=max(0, cfg.translator.context_turns))
        self._background = background
        self._jobs: asyncio.Queue[tuple[_Turn, AsrHypothesis] | None] = asyncio.Queue()
        self._out: asyncio.Queue[PipelineEvent | None] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None

    async def process_frame(self, pcm16: bytes) -> AsyncIterator[PipelineEvent]:
        """Feed one frame of 16kHz mono PCM16 audio; yields zero or more events."""
        for vad_event in self._vad.push(pcm16):
            async for event in self._handle_vad_event(vad_event):
                yield event

        # While an utterance is open, opportunistically run a local-agreement
        # partial decode (internally gated by asr.local_agreement.chunk_ms).
        if not self._emit_partials:
            return
        audio_so_far = self._vad.current_utterance_audio()
        if audio_so_far is not None and self._turn is not None:
            async for event in self._maybe_partial(audio_so_far):
                yield event

    async def flush(self) -> AsyncIterator[PipelineEvent]:
        """Force-close any open utterance (WAV EOF / WS disconnect). In
        background mode, also waits for the queue to finish translating and
        speaking; background_events() ends after that."""
        for vad_event in self._vad.flush():
            async for event in self._handle_vad_event(vad_event):
                yield event
        if self._background:
            self._jobs.put_nowait(None)
            if self._worker is not None:
                await self._worker
            else:
                self._out.put_nowait(None)

    def close(self) -> None:
        """Stops the background queue without finishing it (flush() finishes it)."""
        if self._worker is not None and not self._worker.done():
            self._worker.cancel()

    async def background_events(self) -> AsyncIterator[PipelineEvent]:
        """background=True: the translation, voice and error events, in the
        order things were said. Ends once flush() has emptied the queue."""
        while (event := await self._out.get()) is not None:
            yield event

    def latency_for_turn(self, turn_id: str) -> dict[str, float] | None:
        """Per-stage latency (ms) recorded for a completed turn, for the benchmark script."""
        return self._latency_by_turn.get(turn_id)

    async def _maybe_partial(self, audio_so_far: np.ndarray) -> AsyncIterator[PipelineEvent]:
        # feed() itself is a near-instant no-op on most frames (it only re-decodes
        # every chunk_ms of new audio), so we time every call but only commit a
        # tracker entry / partial event when it actually produced a hypothesis -
        # otherwise the per-turn latency breakdown would be swamped by gate-check
        # no-ops (one per network frame) instead of the handful of real decodes.
        start = time.perf_counter()
        hyp = await self._asr.feed(audio_so_far)
        duration_ms = (time.perf_counter() - start) * 1000
        if hyp is None or not hyp.text or self._turn is None:
            return
        self._partial_index += 1
        self._turn.tracker.record(f"asr_partial[{self._partial_index}]", duration_ms)
        yield self._event(self._turn, EventType.PARTIAL, hyp.language, hyp.text, latency_ms=duration_ms)

    async def _handle_vad_event(self, vad_event: VadEvent) -> AsyncIterator[PipelineEvent]:
        if vad_event.type is VadEventType.SPEECH_START:
            turn_id = new_turn_id()
            self._turn = _Turn(turn_id=turn_id, tracker=LatencyTracker(turn_id=turn_id))
            self._partial_index = 0
            self._asr.start_utterance(turn_id)
            log_event(_LOGGER, logging.INFO, "speech_start", turn_id=turn_id)
            return

        # SPEECH_END
        assert vad_event.audio is not None
        turn, self._turn = self._turn, None
        assert turn is not None
        turn.speech_end_perf = time.perf_counter()

        floor = self._cfg.vad.min_utterance_dbfs
        if floor is not None and (level := rms_dbfs(vad_event.audio)) < floor:
            log_event(_LOGGER, logging.INFO, "utterance_too_quiet", turn_id=turn.turn_id, dbfs=round(level, 1))
            return
        min_voiced = self._cfg.vad.min_voiced_ms
        if min_voiced is not None and (voiced := voiced_ms(vad_event.audio, self._cfg.audio.sample_rate_hz)) < min_voiced:
            log_event(_LOGGER, logging.INFO, "utterance_not_voiced", turn_id=turn.turn_id, voiced_ms=voiced)
            return

        try:
            with turn.tracker.stage("asr_final"):
                hyp = await self._asr.finalize(vad_event.audio)
        except Exception as error:
            yield self._error_event(turn, self._cfg.translator.source_lang, "transcription", error)
            return
        if hyp.text and self._accept_transcript is not None and not self._accept_transcript(hyp.text, hyp.language):
            log_event(_LOGGER, logging.INFO, "transcript_rejected", turn_id=turn.turn_id, lang=hyp.language)
            return
        yield self._event(turn, EventType.FINAL, hyp.language, hyp.text, is_final_segment=True)

        if not hyp.text:
            self._complete(turn)
        elif self._background:
            if self._worker is None:
                self._worker = asyncio.create_task(self._work())
            self._jobs.put_nowait((turn, hyp))
        else:
            async for event in self._translate_and_speak(turn, hyp):
                yield event

    async def _work(self) -> None:
        """background=True: translates and speaks queued phrases, in order."""
        taken: deque[tuple[_Turn, AsrHypothesis] | None] = deque()  # off the queue, not yet handled
        while True:
            job = taken.popleft() if taken else await self._jobs.get()
            if job is None:
                break
            turn, hyp = job
            # Everything else already waiting from the same speaker goes along.
            while not taken and not self._jobs.empty() and len(hyp.text) < MAX_BATCH_CHARS:
                queued = self._jobs.get_nowait()
                if queued is None or queued[1].language != hyp.language:
                    taken.append(queued)
                    break
                later, later_hyp = queued
                later.merged_ids = [*turn.merged_ids, turn.turn_id, *later.merged_ids]
                self._complete(turn)
                turn, hyp = later, AsrHypothesis(text=f"{hyp.text} {later_hyp.text}", language=hyp.language, is_final=True)
            try:
                async for event in self._translate_and_speak(turn, hyp):
                    self._out.put_nowait(event)
            except Exception as error:  # never let one phrase stop the ones after it
                self._out.put_nowait(self._error_event(turn, hyp.language, "translation", error))
        self._out.put_nowait(None)

    async def _translate_and_speak(self, turn: _Turn, hyp: AsrHypothesis) -> AsyncIterator[PipelineEvent]:
        source_lang = hyp.language
        target_lang = (
            self._cfg.translator.target_lang
            if source_lang == self._cfg.translator.source_lang
            else self._cfg.translator.source_lang
        )
        tracker = turn.tracker
        translated_sentences: list[str] = []
        context = list(self._context)
        for i, sentence in enumerate(segment_sentences(hyp.text)):
            # One slow or failed sentence (an Ollama timeout, a TTS hiccup)
            # becomes an ERROR event, not an exception: raised, it would end
            # the caller's session - a meeting bot would go deaf for the
            # rest of the call over one sentence.
            try:
                with tracker.stage(f"translate[{i}]"):
                    translated = await self._translator.translate(sentence, source_lang, target_lang, context=context)
            except Exception as error:
                yield self._error_event(turn, target_lang, "translation", error)
                continue
            translated_sentences.append(translated)
            yield self._event(turn, EventType.TRANSLATION, target_lang, translated, is_final_segment=True)

            if self._tts is not None and (self._should_speak is None or self._should_speak()):
                try:
                    with tracker.stage(f"tts[{i}]"):
                        audio = await self._tts.synthesize(translated, target_lang)
                except Exception as error:
                    yield self._error_event(turn, target_lang, "speech", error)
                    continue
                yield self._event(
                    turn,
                    EventType.AUDIO,
                    target_lang,
                    translated,
                    is_final_segment=True,
                    audio=audio.pcm16,
                    audio_sample_rate=audio.sample_rate,
                )

        if translated_sentences:
            self._context.append(
                TurnContext(
                    source_text=hyp.text,
                    translated_text=" ".join(translated_sentences),
                    source_lang=source_lang,
                    target_lang=target_lang,
                )
            )
        self._complete(turn)

    def _complete(self, turn: _Turn) -> None:
        self._latency_by_turn[turn.turn_id] = turn.tracker.as_dict()
        extra = {"merged_with": turn.merged_ids} if turn.merged_ids else {}
        log_event(_LOGGER, logging.INFO, "turn_complete", turn_id=turn.turn_id, **extra, **turn.tracker.as_dict())

    def _error_event(self, turn: _Turn, lang: str, stage: str, error: Exception) -> PipelineEvent:
        detail = f"{stage} failed: {type(error).__name__}: {error}".rstrip(": ")
        log_event(_LOGGER, logging.ERROR, "pipeline_stage_failed", turn_id=turn.turn_id, stage=stage, error=detail)
        event = self._event(turn, EventType.ERROR, lang, "")
        event.error = detail
        return event

    def _event(
        self,
        turn: _Turn,
        type_: EventType,
        lang: str,
        text: str,
        latency_ms: float | None = None,
        is_final_segment: bool = False,
        audio: bytes | None = None,
        audio_sample_rate: int | None = None,
    ) -> PipelineEvent:
        if latency_ms is None:  # since the speaker stopped
            latency_ms = (time.perf_counter() - turn.speech_end_perf) * 1000 if turn.speech_end_perf else 0.0
        turn.seq += 1
        return PipelineEvent(
            type=type_,
            lang=lang,
            text=text,
            turn_id=turn.turn_id,
            seq=turn.seq,
            latency_ms=round(latency_ms, 2),
            is_final_segment=is_final_segment,
            audio=audio,
            audio_sample_rate=audio_sample_rate,
        )
