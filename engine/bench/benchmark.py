"""Benchmark script: per-stage latency + WER on a sample set.

Usage (run from the engine/ directory):
    python -m bench.benchmark                       # auto-generated espeak-ng smoke set
    python -m bench.benchmark --manifest my.json     # your own recorded audio + references
    python -m bench.benchmark --dry-run              # fake providers; validates the harness only

--manifest expects a JSON file: [{"wav": "relative/or/absolute/path.wav", "reference": "text"}, ...]

Prints, per pipeline stage, mean/median/p95 latency in ms aggregated across all
samples; each sample's "first translated caption" latency (VAD speech_end ->
first TRANSLATION event) against the <=2s budget from the spec; and the
overall WER (word error rate, via jiwer) of the final ASR transcripts against
the reference texts.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
from pathlib import Path

from jiwer import wer

from app.audio_utils import iter_frames, load_wav_as_pcm16_mono
from app.config import EngineConfig, load_config
from app.logging_utils import configure_logging
from app.pipeline import Pipeline
from app.providers.base import build_asr_provider, build_translator_provider
from app.schema import EventType, PipelineEvent
from bench.samples import BenchmarkSample, build_default_manifest, load_manifest

CAPTION_BUDGET_MS = 2000.0


async def run_sample(pipeline: Pipeline, cfg: EngineConfig, sample: BenchmarkSample) -> dict:
    pcm = load_wav_as_pcm16_mono(sample.wav_path, target_sample_rate=cfg.audio.sample_rate_hz)
    frame_samples = cfg.audio.frame_samples

    final_text = ""
    first_caption_latency_ms: float | None = None
    last_turn_id: str | None = None

    def observe(event: PipelineEvent) -> None:
        nonlocal final_text, first_caption_latency_ms, last_turn_id
        if event.type is EventType.FINAL:
            final_text = event.text
            last_turn_id = event.turn_id
        elif event.type is EventType.TRANSLATION and first_caption_latency_ms is None:
            first_caption_latency_ms = event.latency_ms

    for frame in iter_frames(pcm, frame_samples):
        async for event in pipeline.process_frame(frame.tobytes()):
            observe(event)
    async for event in pipeline.flush():
        observe(event)

    stage_latencies = pipeline.latency_for_turn(last_turn_id) if last_turn_id else {}
    return {
        "reference": sample.reference,
        "hypothesis": final_text,
        "first_caption_latency_ms": first_caption_latency_ms,
        "stage_latencies": stage_latencies,
    }


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1)))
    return ordered[idx]


async def _maybe_aclose(provider: object) -> None:
    aclose = getattr(provider, "aclose", None)
    if aclose is not None:
        await aclose()


async def main_async(args: argparse.Namespace) -> None:
    cfg = load_config()
    if args.dry_run:
        cfg.asr.provider = "fake"
        cfg.translator.provider = "fake"
    configure_logging(cfg.logging)

    manifest = load_manifest(Path(args.manifest)) if args.manifest else build_default_manifest()
    if not args.manifest:
        print(
            "No --manifest given: using an auto-generated espeak-ng smoke set.\n"
            "This validates the pipeline and gives latency numbers, but espeak-ng's\n"
            "robotic voice is NOT representative of real speech difficulty - record\n"
            "your own audio + reference transcripts and pass --manifest for a\n"
            "trustworthy WER number.\n"
        )

    asr = build_asr_provider(cfg.asr)
    translator = build_translator_provider(cfg.translator)

    results = []
    for sample in manifest:
        pipeline = Pipeline(cfg, asr, translator)  # fresh VAD/turn state per sample
        result = await run_sample(pipeline, cfg, sample)
        results.append(result)
        status = "OK" if result["hypothesis"] else "EMPTY"
        print(f"[{status}] ref={sample.reference!r} hyp={result['hypothesis']!r}")

    await _maybe_aclose(translator)

    references = [r["reference"] for r in results]
    hypotheses = [r["hypothesis"] or "" for r in results]
    overall_wer = wer(references, hypotheses)

    print("\n=== Latency per stage (ms) ===")
    stage_prefixes = sorted({name.split("[")[0] for r in results for name in r["stage_latencies"]})
    for prefix in stage_prefixes:
        values = [v for r in results for k, v in r["stage_latencies"].items() if k.split("[")[0] == prefix]
        if values:
            print(
                f"  {prefix:14s} mean={statistics.mean(values):7.1f}ms  "
                f"median={statistics.median(values):7.1f}ms  "
                f"p95={_percentile(values, 95):7.1f}ms  n={len(values)}"
            )

    print("\n=== First translated caption latency (VAD speech_end -> first caption) ===")
    caption_latencies = [r["first_caption_latency_ms"] for r in results if r["first_caption_latency_ms"] is not None]
    if caption_latencies:
        mean_latency = statistics.mean(caption_latencies)
        p95_latency = _percentile(caption_latencies, 95)
        verdict = "PASS" if p95_latency <= CAPTION_BUDGET_MS else "FAIL"
        print(f"  mean={mean_latency:.1f}ms  p95={p95_latency:.1f}ms  budget={CAPTION_BUDGET_MS:.0f}ms  [{verdict}]")
    else:
        print("  no samples produced a translated caption")

    print(f"\n=== WER (final ASR transcript vs. reference) ===\n  {overall_wer:.3f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", help='JSON file: [{"wav": "path", "reference": "text"}, ...]')
    parser.add_argument("--dry-run", action="store_true", help="Use fake ASR/translator providers (validates the harness only)")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
