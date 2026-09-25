"""faster-whisper (CTranslate2) ASR provider with local-agreement streaming.

Whisper only supports whole-buffer decoding, not true incremental decode. To
stream partial captions without O(utterance^2) recompute as an utterance grows,
we use the LocalAgreement-2 policy (Macháček et al., "Turning Whisper into
Real-Time Transcription System"): re-decode only the still-unconfirmed audio
tail every `chunk_ms`, and commit a word as confirmed once two consecutive
decodes agree on it - then permanently advance past its audio using the
word's own end timestamp, so later decodes never re-pay for it.

`finalize()` re-decodes the *entire* utterance once for the authoritative
transcript; the stitched partial hypotheses from `feed()` are for live
captions only and are discarded once the segment ends.

Hardware: on CPU, large-v3-turbo will not reliably hit the <=2s partial-caption
budget - see README "Hardware" for the small.en/base.en + int8 fallback.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import numpy as np

from app.audio_utils import pcm16_to_float32
from app.config import REPO_ROOT, AsrConfig
from app.glossary import Glossary
from app.providers.base import AsrHypothesis, AsrProvider
from app.text_normalize import normalize_text


# "<model> on <device>" of the most recently loaded model, and why the GPU
# couldn't be used if it fell back - for the dashboard.
last_loaded: str | None = None
last_gpu_error: str | None = None


def _resolve_device_and_compute_type(cfg: AsrConfig) -> tuple[str, str]:
    device = cfg.device
    if device == "auto":
        import ctranslate2

        device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    compute_type = cfg.compute_type
    if compute_type == "auto":
        compute_type = "float16" if device == "cuda" else "int8"
    return device, compute_type


# What Whisper "hears" in silence, breathing, keyboard noise or a call's comfort
# noise: phrases from the subtitles it was trained on. Dropped only when they
# are the whole utterance - inside a real sentence they're kept. Normalized as
# by normalize_text() (no diacritics, punctuation or case).
KNOWN_HALLUCINATIONS = frozenset(
    {
        # Arabic
        "شكرا", "شكرا لكم", "شكرا لك", "شكرا جزيلا", "شكرا جزيلا لكم",
        "شكرا على المشاهدة", "شكرا لكم على المشاهدة", "شكرا للمشاهدة", "شكرا لمشاهدتكم",
        "اشتركوا في القناة", "اشترك في القناة", "لا تنسوا الاشتراك في القناة",
        "ترجمة نانسي قنقر", "نانسي قنقر", "موسيقى", "تصفيق",
        # English
        "thank you", "thank you very much", "thank you so much", "thanks",
        "thanks for watching", "thank you for watching", "thank you so much for watching",
        "please subscribe", "subscribe", "you", "music", "applause",
        "subtitles by the amaraorg community",
    }
)
def is_known_hallucination(text: str) -> bool:
    return normalize_text(text) in KNOWN_HALLUCINATIONS


def hotwords_by_language(glossary: Glossary) -> dict[str, str]:
    """The glossary's terms as Whisper hint words, one comma-separated list per language."""
    terms = glossary.terms()
    if not terms:
        return {}
    return {"en": ", ".join(t.en for t in terms), "ar": "، ".join(t.ar for t in terms)}


def looks_like_non_speech(no_speech_prob: float, avg_logprob: float, compression_ratio: float) -> bool:
    """Whisper's own per-segment signals that it transcribed something that wasn't speech.

    Stricter than faster-whisper's defaults (which skip a segment only when
    no_speech_prob > 0.6 *and* avg_logprob < -1): meeting audio is mostly
    silence, so a false caption costs more than a missed mumble.
    """
    return (
        no_speech_prob > 0.6
        or (no_speech_prob > 0.3 and avg_logprob < -0.7)
        or avg_logprob < -1.0
        or compression_ratio > 2.4  # the same words over and over
    )


def _common_prefix_len(prev_words: list[str], curr_words: list[str]) -> int:
    n = 0
    for a, b in zip(prev_words, curr_words, strict=False):
        if a != b:
            break
        n += 1
    return n


@dataclass
class _UtteranceSession:
    confirmed_text: str = ""
    confirmed_samples: int = 0
    last_hyp_words: list[str] = field(default_factory=list)
    last_decoded_total_samples: int = 0
    language: str | None = None  # picked once per utterance from candidate_languages


class FasterWhisperAsr(AsrProvider):
    def __init__(self, cfg: AsrConfig, sample_rate: int = 16000) -> None:
        from faster_whisper import WhisperModel

        self._cfg = cfg
        self._sample_rate = sample_rate
        device, compute_type = _resolve_device_and_compute_type(cfg)
        model_name = cfg.model
        try:
            self._model = WhisperModel(model_name, device=device, compute_type=compute_type)
            if device == "cuda":
                # cuBLAS/cuDNN are only loaded by the first inference, so a
                # broken CUDA setup would otherwise surface mid-meeting.
                segments, _ = self._model.transcribe(np.zeros(sample_rate, dtype=np.float32), language="en")
                list(segments)
        except Exception as error:
            if device != "cuda" or not cfg.cpu_fallback_model:
                raise
            import logging

            global last_gpu_error
            last_gpu_error = f"{type(error).__name__}: {error}"[:300]
            logging.getLogger("tts_engine").warning(
                "ASR could not run %s on the GPU (%r); using %s on the CPU instead",
                cfg.model, error, cfg.cpu_fallback_model,
            )
            device, model_name = "cpu", cfg.cpu_fallback_model
            self._model = WhisperModel(model_name, device=device, compute_type="int8")
        global last_loaded
        last_loaded = f"{model_name} on {device}"
        self._hotwords = hotwords_by_language(Glossary.load(REPO_ROOT / cfg.glossary_path if cfg.glossary_path else None))
        self._chunk_samples = max(1, int(cfg.local_agreement.chunk_ms * sample_rate / 1000))
        self._session: _UtteranceSession | None = None
        if cfg.local_agreement.agreement_window != 2:
            import logging

            logging.getLogger("tts_engine").warning(
                "asr.local_agreement.agreement_window=%s requested, but Phase 1 only "
                "implements LocalAgreement-2 (compares consecutive hypothesis pairs); "
                "falling back to window=2.",
                cfg.local_agreement.agreement_window,
            )

    def start_utterance(self, turn_id: str) -> None:
        self._session = _UtteranceSession()

    async def feed(self, utterance_audio_so_far: np.ndarray) -> AsrHypothesis | None:
        session = self._session
        if session is None:
            raise RuntimeError("start_utterance() must be called before feed()")
        if not self._cfg.local_agreement.enabled:
            return None

        total_samples = len(utterance_audio_so_far)
        if total_samples - session.last_decoded_total_samples < self._chunk_samples:
            return None
        session.last_decoded_total_samples = total_samples

        tail_audio = utterance_audio_so_far[session.confirmed_samples :]
        if len(tail_audio) == 0:
            return None

        words, language = await asyncio.to_thread(self._decode_words, tail_audio)
        word_texts = [w.word for w in words]
        agree_len = _common_prefix_len(session.last_hyp_words, word_texts)

        if agree_len > 0:
            newly_confirmed = words[:agree_len]
            session.confirmed_text += "".join(w.word for w in newly_confirmed)
            session.confirmed_samples += int(newly_confirmed[-1].end * self._sample_rate)

        session.last_hyp_words = word_texts[agree_len:]
        tentative_tail = "".join(w.word for w in words[agree_len:])
        text = (session.confirmed_text + tentative_tail).strip()
        return AsrHypothesis(text=text, language=language, is_final=False)

    async def finalize(self, full_utterance_audio: np.ndarray) -> AsrHypothesis:
        if self._session is None:
            raise RuntimeError("start_utterance() must be called before finalize()")
        text, language = await asyncio.to_thread(self._decode_text, full_utterance_audio)
        self._session = None
        return AsrHypothesis(text=text, language=language, is_final=True)

    def _decode_words(self, audio_int16: np.ndarray) -> tuple[list, str]:
        """Word-level decode (word_timestamps=True), used for local-agreement partials."""
        session = self._session
        audio = pcm16_to_float32(audio_int16)
        language = session.language if session is not None and session.language else self._resolved_language(audio)
        if session is not None:
            session.language = language
        segments, info = self._model.transcribe(
            audio,
            language=language,
            beam_size=self._cfg.beam_size,
            word_timestamps=True,
        )
        words = [w for seg in segments for w in (seg.words or [])]
        return words, info.language

    def _decode_text(self, audio_int16: np.ndarray) -> tuple[str, str]:
        """Plain segment-level decode, used for the authoritative final transcript.

        Returns "" when there was no real speech in it - see the filters above.
        """
        audio = pcm16_to_float32(audio_int16)
        language = self._resolved_language(audio)  # re-picked on the whole utterance
        segments, info = self._model.transcribe(
            audio,
            language=language,
            beam_size=self._cfg.beam_size,
            word_timestamps=False,
            hotwords=self._hotwords.get(language or ""),
            # Whisper's own speech detector trims the noise our endpointer let
            # through; with nothing left it returns no segments instead of
            # inventing some. One utterance at a time, so nothing to condition on.
            vad_filter=True,
            condition_on_previous_text=False,
        )
        kept = [
            seg for seg in segments
            if not looks_like_non_speech(seg.no_speech_prob, seg.avg_logprob, seg.compression_ratio)
        ]
        text = "".join(seg.text for seg in kept).strip()
        if is_known_hallucination(text):
            text = ""
        return text, info.language

    def _resolved_language(self, audio: np.ndarray) -> str | None:
        """The language to decode in: fixed by config, the likeliest candidate, or
        None to let Whisper pick from every language it knows."""
        if self._cfg.language != "auto":
            return self._cfg.language
        if not self._cfg.candidate_languages:
            return None
        _, _, all_probs = self._model.detect_language(audio=audio)
        probs = dict(all_probs)
        return max(self._cfg.candidate_languages, key=lambda lang: probs.get(lang, 0.0))
