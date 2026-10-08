"""Speech recognition on many voices and many bad-audio conditions.

For every model you name it transcribes the same test clips and reports word
and character error rates (WER / CER), language-detection accuracy and speed:

  voices      male and female, English (US / UK / Australian) and Arabic
              (Jordanian, Lebanese, Syrian, Egyptian, Saudi, Emirati,
              Moroccan ...), spoken by Microsoft's neural voices (edge-tts,
              needs internet; clips are cached in --cache so reruns are free)
  conditions  clean | very quiet (-35 dBFS) | quiet (-24) | loud + clipped |
              deep voice (pitch x0.8) | high voice (pitch x1.3) | no bass
              (high-pass 400 Hz) | boomy bass (+12 dB under 250 Hz) | phone
              line (300-3400 Hz) | noisy (10 dB / 3 dB signal-to-noise)
  your audio  --manifest FILE.jsonl, lines {"wav": "...", "text": "...",
              "lang": "ar"|"en"} - real recordings beat synthetic voices,
              and are the only honest test of Jordanian dialect

Pitch is changed by resampling, so a "deep voice" also speaks a little slower;
the bass cut/boost are frequency-domain filters. Synthetic voices are clean and
read-aloud, so absolute numbers are friendlier than real meetings - compare
models and conditions with each other, not with the published benchmarks.

Models (--model, repeat for several):
  cohere:REPO[@PROCESSOR_REPO]   Cohere Transcribe family, e.g.
        cohere:CohereLabs/cohere-transcribe-03-2026
        cohere:oddadmix/cohere-transcribe-arabic-07-2026-dialectal-v2@Newmetrics/cohere-transcribe-arabic-07-2026
        (needs an NVIDIA GPU; gated repos need HF_TOKEN)
  whisper:NAME                   faster-whisper, e.g. whisper:large-v3-turbo,
        whisper:small (runs on the processor with --cpu)

    python test_asr_voices.py --model cohere:CohereLabs/cohere-transcribe-03-2026 \\
        --model whisper:large-v3-turbo --json asr_results.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
import time
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import AsrConfig, CohereAsrConfig  # noqa: E402

SR = 16000

SENTENCES = {
    "en": [
        "Good morning, can you send me the catalogue this afternoon?",
        "The walnut dining table costs four thousand eight hundred and fifty dinars.",
        "We need to confirm the shipment before Thursday, otherwise the container will miss the vessel.",
    ],
    "ar": [
        "صباح الخير، هل يمكنك أن ترسل لي الكتالوج بعد الظهر؟",
        "تكلف طاولة الطعام من خشب الجوز أربعة آلاف وثمانمئة وخمسين ديناراً.",
        "نحتاج إلى تأكيد الشحنة قبل يوم الخميس، وإلا ستفوت الحاوية السفينة.",
    ],
}

# edge-tts voices, tried in this order; missing ones are skipped (the service renames voices now and then)
VOICES = {
    "en": [
        ("en-US-AndrewNeural", "m"), ("en-US-AriaNeural", "f"), ("en-GB-RyanNeural", "m"), ("en-GB-SoniaNeural", "f"),
        ("en-AU-WilliamNeural", "m"), ("en-AU-NatashaNeural", "f"), ("en-IN-PrabhatNeural", "m"), ("en-IE-EmilyNeural", "f"),
    ],
    "ar": [
        ("ar-JO-TaimNeural", "m"), ("ar-JO-SanaNeural", "f"), ("ar-LB-RamiNeural", "m"), ("ar-LB-LaylaNeural", "f"),
        ("ar-SY-LaithNeural", "m"), ("ar-SY-AmanyNeural", "f"), ("ar-EG-ShakirNeural", "m"), ("ar-EG-SalmaNeural", "f"),
        ("ar-SA-HamedNeural", "m"), ("ar-SA-ZariyahNeural", "f"), ("ar-AE-HamdanNeural", "m"), ("ar-AE-FatimaNeural", "f"),
        ("ar-MA-JamalNeural", "m"), ("ar-MA-MounaNeural", "f"),
    ],
}

# ------------------------------------------------------------------ audio conditions


def dbfs(x: np.ndarray) -> float:
    return 20 * np.log10(max(float(np.sqrt(np.mean(x**2))), 1e-9))


def to_dbfs(x: np.ndarray, target: float) -> np.ndarray:
    return x * 10 ** ((target - dbfs(x)) / 20)


def fft_filter(x: np.ndarray, gain) -> np.ndarray:
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(len(x), 1 / SR)
    return np.fft.irfft(spec * gain(freqs), n=len(x))


def resample_pitch(x: np.ndarray, factor: float) -> np.ndarray:
    """factor > 1 = higher voice (and faster); < 1 = deeper (and slower)."""
    idx = np.arange(0, len(x) - 1, factor)
    return np.interp(idx, np.arange(len(x)), x)


def add_noise(x: np.ndarray, snr_db: float, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    white = rng.standard_normal(len(x))
    pink = fft_filter(white, lambda f: 1 / np.sqrt(np.maximum(f, 20.0)))  # 1/f noise: room / fan rumble
    noise = to_dbfs(pink, dbfs(x) - snr_db)
    return x + noise


CONDITIONS = {
    "clean": lambda x: to_dbfs(x, -20),
    "very quiet (-35 dBFS)": lambda x: to_dbfs(x, -35),
    "quiet (-24 dBFS)": lambda x: to_dbfs(x, -24),
    "loud + clipped": lambda x: np.clip(to_dbfs(x, -6) * 3.0, -1, 1),
    "deep voice (x0.8)": lambda x: to_dbfs(resample_pitch(x, 0.8), -20),
    "high voice (x1.3)": lambda x: to_dbfs(resample_pitch(x, 1.3), -20),
    "no bass (HP 400 Hz)": lambda x: to_dbfs(fft_filter(x, lambda f: 1 / np.sqrt(1 + (400 / np.maximum(f, 1)) ** 8)), -20),
    "boomy bass (+12 dB <250 Hz)": lambda x: to_dbfs(fft_filter(x, lambda f: 1 + 3.0 / (1 + (f / 250) ** 4)), -20),
    "phone line (300-3400 Hz)": lambda x: to_dbfs(fft_filter(x, lambda f: ((f > 300) & (f < 3400)).astype(float)), -20),
    "noisy (SNR 10 dB)": lambda x: to_dbfs(add_noise(to_dbfs(x, -20), 10), -20),
    "very noisy (SNR 3 dB)": lambda x: to_dbfs(add_noise(to_dbfs(x, -20), 3), -20),
}


def apply_condition(audio16: np.ndarray, name: str) -> np.ndarray:
    """int16 mono in -> int16 mono out, with a 0.4 s silent lead-in/out like a VAD-cut phrase."""
    x = audio16.astype(np.float64) / 32768.0
    pad = np.zeros(int(0.4 * SR))
    x = np.concatenate([pad, x, pad])
    y = CONDITIONS[name](x)
    if name != "loud + clipped" and np.max(np.abs(y)) > 1:  # never clip except on purpose
        y = y / np.max(np.abs(y)) * 0.98
    return (np.clip(y, -1, 1) * 32767).astype(np.int16)


# ------------------------------------------------------------------ scoring


_DIACRITICS = re.compile(r"[ً-ْٰـ]")
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def normalize(text: str, lang: str) -> str:
    t = unicodedata.normalize("NFKC", text).lower().translate(_AR_DIGITS)
    if lang == "ar":
        t = _DIACRITICS.sub("", t)
        t = re.sub("[إأآٱ]", "ا", t).replace("ى", "ي").replace("ة", "ه").replace("ؤ", "و").replace("ئ", "ي")
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"(?<=\d)\s*,\s*(?=\d)", "", t)
    return re.sub(r"\s+", " ", t).strip()


def edit_distance(a: list, b: list) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def wer_cer(hyp: str, ref: str, lang: str) -> tuple[float, float]:
    h, r = normalize(hyp, lang), normalize(ref, lang)
    rw, hw = r.split(), h.split()
    wer = edit_distance(hw, rw) / max(1, len(rw))
    cer = edit_distance(list(h.replace(" ", "")), list(r.replace(" ", ""))) / max(1, len(r.replace(" ", "")))
    return min(wer, 3.0), min(cer, 3.0)  # a runaway transcript shouldn't swamp the average


# ------------------------------------------------------------------ test clips


@dataclass
class Clip:
    voice: str
    gender: str
    lang: str
    text: str
    path: Path


async def synthesize(voices_per_lang: int, cache: Path) -> list[Clip]:
    import edge_tts

    cache.mkdir(parents=True, exist_ok=True)
    clips: list[Clip] = []
    try:
        available = {v["ShortName"] for v in await edge_tts.list_voices()}
    except Exception as error:
        print(f"voice service unreachable ({error}); using cached clips only")
        available = None
    for lang, voices in VOICES.items():
        used = 0
        for voice, gender in voices:
            if used >= voices_per_lang:
                break
            if available is not None and voice not in available:
                continue
            got = 0
            for i, text in enumerate(SENTENCES[lang]):
                path = cache / f"{voice}-{i}.wav"
                if not path.exists():
                    if available is None:
                        continue
                    data = bytearray()
                    async for chunk in edge_tts.Communicate(text, voice).stream():
                        if chunk["type"] == "audio":
                            data += chunk["data"]
                    audio, sr = sf.read(__import__("io").BytesIO(bytes(data)), dtype="float32", always_2d=True)
                    mono = audio.mean(axis=1)
                    mono = np.interp(np.linspace(0, len(mono) - 1, int(len(mono) * SR / sr)), np.arange(len(mono)), mono)
                    sf.write(path, (np.clip(mono, -1, 1) * 32767).astype(np.int16), SR)
                clips.append(Clip(voice, gender, lang, text, path))
                got += 1
            used += 1 if got else 0
    return clips


def load_manifest(path: Path) -> list[Clip]:
    clips = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            clips.append(Clip(d.get("voice", Path(d["wav"]).stem), d.get("gender", "?"), d["lang"], d["text"], Path(d["wav"])))
    return clips


def read_16k(path: Path) -> np.ndarray:
    audio, sr = sf.read(path, dtype="float32", always_2d=True)
    mono = audio.mean(axis=1)
    if sr != SR:
        mono = np.interp(np.linspace(0, len(mono) - 1, int(len(mono) * SR / sr)), np.arange(len(mono)), mono)
    return (np.clip(mono, -1, 1) * 32767).astype(np.int16)


# ------------------------------------------------------------------ models


def build_asr(spec: str, cpu: bool):
    kind, _, name = spec.partition(":")
    if kind == "cohere":
        repo, _, processor = name.partition("@")
        from app.providers.asr_cohere import CohereAsr

        # a repo that ships its own processor + modeling code (CohereLabs/cohere-transcribe-03-2026,
        # or a folder you downloaded it into) loads with trust_remote_code; the dialect fine-tune
        # (which names a processor repo) uses transformers' built-in class
        cfg = AsrConfig(
            provider="cohere", language="auto", candidate_languages=["en", "ar"],
            cohere=CohereAsrConfig(model=repo, processor=processor or None, quantize="none", trust_remote_code=not processor),
        )
        return CohereAsr(cfg)
    if kind == "whisper":
        from app.providers.asr_faster_whisper import FasterWhisperAsr

        cfg = AsrConfig(
            provider="faster_whisper", model=name, device="cpu" if cpu else "auto",
            compute_type="int8" if cpu else "auto", language="auto", candidate_languages=["en", "ar"], beam_size=1,
        )
        return FasterWhisperAsr(cfg)
    sys.exit(f"unknown model {spec!r}: use cohere:REPO[@PROCESSOR] or whisper:NAME")


@dataclass
class Row:
    model: str
    voice: str
    gender: str
    lang: str
    condition: str
    wer: float
    cer: float
    lang_ok: bool
    ms: float
    audio_s: float
    hyp: str
    ref: str
    error: str | None = None


async def run_model(spec: str, clips: list[Clip], conditions: list[str], cpu: bool) -> list[Row]:
    print(f"\n=== {spec} ===")
    t0 = time.perf_counter()
    try:
        asr = build_asr(spec, cpu)
    except Exception as error:
        print(f"  cannot load: {type(error).__name__}: {error}")
        if "gated" in str(error).lower() or "401" in str(error):
            print("  (gated model: accept its terms on its Hugging Face page, then `hf auth login` or set HF_TOKEN)")
        return []
    print(f"  loaded in {time.perf_counter() - t0:.1f}s")
    rows: list[Row] = []
    base = {id(c): read_16k(c.path) for c in clips}
    await _transcribe(asr, np.zeros(SR, dtype=np.int16))  # warm-up
    for cond in conditions:
        for clip in clips:
            audio = apply_condition(base[id(clip)], cond)
            start = time.perf_counter()
            try:
                text, lang = await _transcribe(asr, audio)
                err = None
            except Exception as error:
                text, lang, err = "", "?", f"{type(error).__name__}: {error}"
            ms = (time.perf_counter() - start) * 1000
            wer, cer = wer_cer(text, clip.text, clip.lang)
            rows.append(Row(spec, clip.voice, clip.gender, clip.lang, cond, round(wer, 3), round(cer, 3), lang == clip.lang, round(ms), round(len(audio) / SR, 2), text, clip.text, err))
        sub = [r for r in rows if r.condition == cond]
        print(f"  {cond:<30} WER {statistics.fmean(r.wer for r in sub):6.1%}  CER {statistics.fmean(r.cer for r in sub):6.1%}  lang {sum(r.lang_ok for r in sub) / len(sub):4.0%}  {statistics.median(r.ms for r in sub):6.0f} ms")
    return rows


async def _transcribe(asr, audio: np.ndarray) -> tuple[str, str]:
    asr.start_utterance("t")
    hyp = await asr.finalize(audio)
    return hyp.text, hyp.language


# ------------------------------------------------------------------ report


def table(title: str, rows: list[Row], key) -> None:
    groups: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        groups[key(r)].append(r)
    print(f"\n{title}")
    print(f"  {'':<34} {'WER':>7} {'CER':>7} {'lang ok':>8} {'median ms':>10}")
    for k, rs in groups.items():
        print(f"  {k:<34} {statistics.fmean(r.wer for r in rs):>7.1%} {statistics.fmean(r.cer for r in rs):>7.1%} {sum(r.lang_ok for r in rs) / len(rs):>8.0%} {statistics.median(r.ms for r in rs):>10.0f}")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", action="append", required=True, help="cohere:REPO[@PROCESSOR] or whisper:NAME (repeat)")
    ap.add_argument("--voices-per-lang", type=int, default=6, help="synthetic voices per language")
    ap.add_argument("--conditions", help="comma-separated subset of the condition names (substring match)")
    ap.add_argument("--manifest", type=Path, help="JSONL of your own recordings; replaces the synthetic voices")
    ap.add_argument("--cache", type=Path, default=Path("asr_test_clips"))
    ap.add_argument("--cpu", action="store_true", help="run whisper models on the processor")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    clips = load_manifest(args.manifest) if args.manifest else await synthesize(args.voices_per_lang, args.cache)
    if not clips:
        sys.exit("no test clips (no internet for the voice service and nothing cached)")
    conds = [c for c in CONDITIONS if not args.conditions or any(s.strip().lower() in c.lower() for s in args.conditions.split(","))]
    print(f"{len(clips)} clips ({len({c.voice for c in clips})} voices) x {len(conds)} conditions x {len(args.model)} model(s)")

    all_rows: list[Row] = []
    for spec in args.model:
        all_rows += await run_model(spec, clips, conds, args.cpu)
    if not all_rows:
        return 1
    for spec in args.model:
        rows = [r for r in all_rows if r.model == spec]
        if not rows:
            continue
        print(f"\n################ {spec}")
        table("by condition", rows, lambda r: r.condition)
        table("by language", rows, lambda r: {"en": "English", "ar": "Arabic"}[r.lang])
        table("by gender", rows, lambda r: {"m": "male voices", "f": "female voices"}.get(r.gender, r.gender))
        table("by voice", rows, lambda r: r.voice)
        worst = sorted(rows, key=lambda r: -r.wer)[:5]
        print("\nworst transcripts:")
        for r in worst:
            print(f"  WER {r.wer:.0%} [{r.voice}, {r.condition}]\n    ref: {r.ref}\n    hyp: {r.hyp}" + (f"\n    error: {r.error}" if r.error else ""))
    if len(args.model) > 1:
        print("\nmodel comparison (all clips and conditions):")
        for spec in args.model:
            rows = [r for r in all_rows if r.model == spec]
            if rows:
                print(f"  {spec:<70} WER {statistics.fmean(r.wer for r in rows):6.1%}  CER {statistics.fmean(r.cer for r in rows):6.1%}  lang {sum(r.lang_ok for r in rows) / len(rows):4.0%}  {statistics.median(r.ms for r in rows):6.0f} ms")
    if args.json:
        args.json.write_text(json.dumps([asdict(r) for r in all_rows], ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nWrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
