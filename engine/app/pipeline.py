"""Orchestrates VAD -> ASR (local-agreement streaming) -> segmenter -> translator
-> TTS into a transport-agnostic stream of PipelineEvents. Bidirectional: ASR
auto-detects the spoken language and translation/TTS direction follows
automatically (see the source_lang/target_lang resolution below). A rolling
window of prior turns (config.translator.context_turns) is fed back to the
translator for cross-turn consistency.

Used identically by the WebSocket server (server.py) and the CLI harnesses
(cli/translate_wav.py, cli/translate_mic.py): none of them know about VAD, ASR,
translator or TTS internals, they just push PCM16 frames in and read events out.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import AsyncIterator, Callable

import numpy as np

from app.config import EngineConfig
from app.logging_utils import LatencyTracker, get_logger, log_event
from app.providers.base import AsrProvider, TranslatorProvider, TtsProvider, TurnContext
from app.segmenter import segment as segment_sentences
from app.schema import EventType, PipelineEvent, new_turn_id
from app.vad import SpeechEndpointer, VadEvent, VadEventType
from app.voicing import voiced_ms

_LOGGER = get_logger()


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
        self._turn_id: str | None = None
        self._seq = 0
        self._speech_end_perf: float | None = None
        self._tracker: LatencyTracker | None = None
        self._latency_by_turn: dict[str, dict[str, float]] = {}
        self._partial_index = 0
        # Rolling context window of prior turns, fed back to the translator for
        # consistency (pronoun resolution, terminology) across the conversation.
        self._context: deque[TurnContext] = deque(maxlen=max(0, cfg.translator.context_turns))

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
        if audio_so_far is not None and self._turn_id is not None:
            async for event in self._maybe_partial(audio_so_far):
                yield event

    async def flush(self) -> AsyncIterator[PipelineEvent]:
        """Force-close any open utterance (WAV EOF / WS disconnect)."""
        for vad_event in self._vad.flush():
            async for event in self._handle_vad_event(vad_event):
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
        if hyp is None or not hyp.text:
            return
        self._partial_index += 1
        if self._tracker is not None:
            self._tracker.record(f"asr_partial[{self._partial_index}]", duration_ms)
        yield self._event(EventType.PARTIAL, hyp.language, hyp.text, duration_ms)

    async def _handle_vad_event(self, vad_event: VadEvent) -> AsyncIterator[PipelineEvent]:
        if vad_event.type is VadEventType.SPEECH_START:
            self._turn_id = new_turn_id()
            self._seq = 0
            self._partial_index = 0
            self._tracker = LatencyTracker(turn_id=self._turn_id)
            self._asr.start_utterance(self._turn_id)
            log_event(_LOGGER, logging.INFO, "speech_start", turn_id=self._turn_id)
            return

        # SPEECH_END
        assert vad_event.audio is not None
        assert self._turn_id is not None
        turn_id = self._turn_id
        tracker = self._tracker
        assert tracker is not None
        self._speech_end_perf = time.perf_counter()

        floor = self._cfg.vad.min_utterance_dbfs
        if floor is not None and (level := rms_dbfs(vad_event.audio)) < floor:
            log_event(_LOGGER, logging.INFO, "utterance_too_quiet", turn_id=turn_id, dbfs=round(level, 1))
            self._turn_id = None
            self._tracker = None
            return
        min_voiced = self._cfg.vad.min_voiced_ms
        if min_voiced is not None and (voiced := voiced_ms(vad_event.audio, self._cfg.audio.sample_rate_hz)) < min_voiced:
            log_event(_LOGGER, logging.INFO, "utterance_not_voiced", turn_id=turn_id, voiced_ms=voiced)
            self._turn_id = None
            self._tracker = None
            return

        try:
            with tracker.stage("asr_final"):
                hyp = await self._asr.finalize(vad_event.audio)
        except Exception as error:
            yield self._error_event(self._cfg.translator.source_lang, turn_id, "transcription", error)
            self._turn_id = None
            self._tracker = None
            return
        if hyp.text and self._accept_transcript is not None and not self._accept_transcript(hyp.text, hyp.language):
            log_event(_LOGGER, logging.INFO, "transcript_rejected", turn_id=turn_id, lang=hyp.language)
            self._turn_id = None
            self._tracker = None
            return
        yield self._event(
            EventType.FINAL,
            hyp.language,
            hyp.text,
            self._elapsed_since_speech_end_ms(),
            turn_id=turn_id,
            is_final_segment=True,
        )

        if hyp.text:
            source_lang = hyp.language
            target_lang = (
                self._cfg.translator.target_lang
                if source_lang == self._cfg.translator.source_lang
                else self._cfg.translator.source_lang
            )
            translated_sentences: list[str] = []
            context = list(self._context)
            for i, sentence in enumerate(segment_sentences(hyp.text)):
                # One slow or failed sentence (an Ollama timeout, a TTS hiccup)
                # becomes an ERROR event, not an exception: raised, it would end
                # the caller's session - a meeting bot would go deaf for the
                # rest of the call over one sentence.
                try:
                    with tracker.stage(f"translate[{i}]"):
                        translated = await self._translator.translate(
                            sentence, source_lang, target_lang, context=context
                        )
                except Exception as error:
                    yield self._error_event(target_lang, turn_id, "translation", error)
                    continue
                translated_sentences.append(translated)
                yield self._event(
                    EventType.TRANSLATION,
                    target_lang,
                    translated,
                    self._elapsed_since_speech_end_ms(),
                    turn_id=turn_id,
                    is_final_segment=True,
                )

                if self._tts is not None and (self._should_speak is None or self._should_speak()):
                    try:
                        with tracker.stage(f"tts[{i}]"):
                            audio = await self._tts.synthesize(translated, target_lang)
                    except Exception as error:
                        yield self._error_event(target_lang, turn_id, "speech", error)
                        continue
                    yield self._event(
                        EventType.AUDIO,
                        target_lang,
                        translated,
                        self._elapsed_since_speech_end_ms(),
                        turn_id=turn_id,
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

        self._latency_by_turn[turn_id] = tracker.as_dict()
        log_event(_LOGGER, logging.INFO, "turn_complete", turn_id=turn_id, **tracker.as_dict())
        self._turn_id = None
        self._tracker = None

    def _error_event(self, lang: str, turn_id: str, stage: str, error: Exception) -> PipelineEvent:
        detail = f"{stage} failed: {type(error).__name__}: {error}".rstrip(": ")
        log_event(_LOGGER, logging.ERROR, "pipeline_stage_failed", turn_id=turn_id, stage=stage, error=detail)
        event = self._event(EventType.ERROR, lang, "", self._elapsed_since_speech_end_ms(), turn_id=turn_id)
        event.error = detail
        return event

    def _elapsed_since_speech_end_ms(self) -> float:
        if self._speech_end_perf is None:
            return 0.0
        return (time.perf_counter() - self._speech_end_perf) * 1000

    def _event(
        self,
        type_: EventType,
        lang: str,
        text: str,
        latency_ms: float,
        turn_id: str | None = None,
        is_final_segment: bool = False,
        audio: bytes | None = None,
        audio_sample_rate: int | None = None,
    ) -> PipelineEvent:
        tid = turn_id or self._turn_id or "unknown"
        self._seq += 1
        return PipelineEvent(
            type=type_,
            lang=lang,
            text=text,
            turn_id=tid,
            seq=self._seq,
            latency_ms=round(latency_ms, 2),
            is_final_segment=is_final_segment,
            audio=audio,
            audio_sample_rate=audio_sample_rate,
        )
