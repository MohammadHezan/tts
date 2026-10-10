"""Cohere Transcribe Arabic (Apache-2.0, a 2B Conformer encoder-decoder) for
speech recognition.

Why: on real Jordanian speech (Casablanca's Jordan test set, 120 clips) the
dialect fine-tune of this model got 26% word / 6.6% character errors, against
44% / 15% for Whisper large-v3-turbo, while being as good at English (FLEURS
4.7%, Arabic-accented English 6.0%) - and ~0.3s a phrase in 8-bit on an RTX 5070.

It can't tell languages apart by itself, so every phrase is transcribed as
each candidate language and the one the model is surest of wins (mean log-
probability of its own tokens). A wrong-language attempt tends to fall into a
loop ("I'm sorry. I'm sorry. I'm sorry...") that scores deceptively well, so a
looping attempt never wins. On 270 phrase-length pieces (1.5-3.5s) this picked
the right language 99.3% of the time; Whisper small's language detection 94.1%.

The model only transcribes: it has no "that was not speech" signal, so the
pipeline's own gates (Silero VAD, loudness, voicing) and the filters below
stand between it and noise it would happily transcribe.
"""

from __future__ import annotations

import asyncio
import logging
import zlib

import numpy as np

from app.audio_utils import pcm16_to_float32
from app.config import AsrConfig
from app.providers import asr_faster_whisper
from app.providers.asr_faster_whisper import is_known_hallucination
from app.providers.base import AsrHypothesis, AsrProvider

_LOGGER = logging.getLogger("tts_engine")
TOKENS_PER_SECOND = 12  # far more than anyone says; stops a decode stuck in a loop
MAX_COMPRESSION_RATIO = 2.4  # the same words over and over


def looks_like_a_loop(text: str) -> bool:
    data = text.encode("utf-8")
    return len(data) > 40 and len(data) / len(zlib.compress(data)) > MAX_COMPRESSION_RATIO


class CohereAsr(AsrProvider):
    def __init__(self, cfg: AsrConfig, sample_rate: int = 16000) -> None:
        import torch
        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, CohereAsrForConditionalGeneration

        c = cfg.cohere
        self._cfg = cfg
        self._sample_rate = sample_rate
        self._torch = torch
        self._processor = AutoProcessor.from_pretrained(
            c.processor or c.model,
            revision=c.processor_revision if c.processor else c.revision,
            cache_dir=c.cache_dir,
            trust_remote_code=c.trust_remote_code,
        )
        # Model class: the repo's own code (trust_remote_code) or the built-in one.
        model_class = AutoModelForSpeechSeq2Seq if c.trust_remote_code else CohereAsrForConditionalGeneration
        model = model_class.from_pretrained(
            c.model, revision=c.revision, cache_dir=c.cache_dir, dtype=torch.bfloat16,
            trust_remote_code=c.trust_remote_code,
            device_map="cpu" if c.quantize == "int8" else ("auto" if c.trust_remote_code else "cuda"),
        )
        if c.quantize == "int8":
            # Weights in 8 bits: half the video memory (2.4GB instead of 4.3GB,
            # room for the translation model), same accuracy in our tests.
            # Converted layer by layer on the way to the GPU: loading the full
            # model there first leaves its 4.3GB reserved for good (fragmented).
            from torchao.quantization import Int8WeightOnlyConfig, quantize_

            quantize_(model, Int8WeightOnlyConfig(), device="cuda")
            model = model.to("cuda")
        self._model = model.eval()
        # Loads the CUDA kernels now rather than in the meeting's first sentence.
        self._final_text(np.zeros(sample_rate, dtype=np.float32))
        asr_faster_whisper.last_loaded = f"{c.model.rstrip('/').split('/')[-1]} ({c.quantize}) on {self._model.device}"

    def start_utterance(self, turn_id: str) -> None:
        pass

    async def feed(self, utterance_audio_so_far: np.ndarray) -> AsrHypothesis | None:
        return None  # whole-phrase decodes only: phrases are short and this is fast

    async def finalize(self, full_utterance_audio: np.ndarray) -> AsrHypothesis:
        text, language = await asyncio.to_thread(self._final_text, pcm16_to_float32(full_utterance_audio))
        return AsrHypothesis(text=text, language=language, is_final=True)

    def _final_text(self, audio: np.ndarray) -> tuple[str, str]:
        if self._cfg.language != "auto":
            languages = [self._cfg.language]
        else:
            languages = self._cfg.candidate_languages or ["en", "ar"]
        best: tuple[float, str, str] | None = None
        for language, (text, score) in zip(languages, self._transcribe_all(audio, languages)):
            if looks_like_a_loop(text):
                score = float("-inf")
            if best is None or score > best[0]:
                best = (score, text, language)
        assert best is not None
        score, text, language = best
        if score == float("-inf") or is_known_hallucination(text):
            text = ""
        return text, language

    def _transcribe_all(self, audio: np.ndarray, languages: list[str]) -> list[tuple[str, float]]:
        """(transcript, confidence) for each language. With several, they are decoded in ONE
        batched pass - the model is memory-bound, so two decodes together cost about as much
        as one and a half (measured: 438 ms instead of 853 ms) - falling back to one at a
        time if the batch can't be built."""
        if len(languages) > 1 and self._cfg.cohere.batch_languages:
            try:
                return self._transcribe_batch(audio, languages)
            except Exception as error:  # the old way is slower, not different
                self._cfg.cohere.batch_languages = False
                logging.getLogger(__name__).warning("batched decoding failed (%r); decoding one language at a time", error)
        return [self._transcribe(audio, language) for language in languages]

    def _transcribe_batch(self, audio: np.ndarray, languages: list[str]) -> list[tuple[str, float]]:
        torch = self._torch
        model = self._model
        per_language = [
            self._processor(audio, sampling_rate=self._sample_rate, return_tensors="pt", language=language) for language in languages
        ]
        merged = {}
        for key in per_language[0].keys():
            first = per_language[0][key]
            if hasattr(first, "shape"):
                value = torch.cat([item[key] for item in per_language], dim=0)
                merged[key] = value.to(model.device, dtype=model.dtype) if value.is_floating_point() else value.to(model.device)
            else:
                merged[key] = [x for item in per_language for x in item[key]]
        max_tokens = 16 + int(TOKENS_PER_SECOND * len(audio) / self._sample_rate)
        with torch.inference_mode():
            out = model.generate(**merged, max_new_tokens=max_tokens, output_scores=True, return_dict_in_generate=True)
            logprobs = model.compute_transition_scores(out.sequences, out.scores, normalize_logits=True)
        eos = model.generation_config.eos_token_id
        eos = set(eos if isinstance(eos, (list, tuple)) else [eos])
        n_prompt = out.sequences.shape[1] - logprobs.shape[1]
        results = []
        for i in range(len(languages)):
            generated = out.sequences[i, n_prompt:].tolist()
            stop = next((j for j, token in enumerate(generated) if token in eos), len(generated) - 1)
            row = logprobs[i, : stop + 1]  # the padding after this language's own end doesn't count
            row = row[torch.isfinite(row)]
            text = self._processor.decode(out.sequences[i : i + 1], skip_special_tokens=True)
            if isinstance(text, list):
                text = " ".join(text)
            results.append((text.strip(), float(row.mean()) if len(row) else float("-inf")))
        return results

    def _transcribe(self, audio: np.ndarray, language: str) -> tuple[str, float]:
        """The transcript in `language` and how sure the model is of it (mean log-probability per token)."""
        torch = self._torch
        model = self._model
        inputs = self._processor(audio, sampling_rate=self._sample_rate, return_tensors="pt", language=language)
        inputs.to(model.device, dtype=model.dtype)
        max_tokens = 16 + int(TOKENS_PER_SECOND * len(audio) / self._sample_rate)
        with torch.inference_mode():
            out = model.generate(**inputs, max_new_tokens=max_tokens, output_scores=True, return_dict_in_generate=True)
            logprobs = model.compute_transition_scores(out.sequences, out.scores, normalize_logits=True)[0]
        logprobs = logprobs[torch.isfinite(logprobs)]
        text = self._processor.decode(out.sequences, skip_special_tokens=True)
        if isinstance(text, list):
            text = " ".join(text)
        return text.strip(), float(logprobs.mean()) if len(logprobs) else float("-inf")


def build(cfg: AsrConfig) -> AsrProvider:
    """CohereAsr, or - if it can't load (no GPU, model not downloaded) - the
    Whisper setup it replaced, so the bot never goes deaf over it."""
    try:
        return CohereAsr(cfg)
    except Exception as error:
        asr_faster_whisper.last_gpu_error = f"{cfg.cohere.model}: {type(error).__name__}: {error}"[:300]
        _LOGGER.warning("Cohere ASR could not load (%r); using Whisper %s instead", error, cfg.model)
        return asr_faster_whisper.FasterWhisperAsr(cfg)
