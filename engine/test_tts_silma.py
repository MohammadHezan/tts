"""Speed and pronunciation of SILMA TTS (open, 150M, Arabic + English) against
the voices the bot uses today (Microsoft neural voices through edge-tts).

For each test sentence it measures
  - time to a finished clip, clip length, real-time factor (time / length;
    below 1 = faster than speech) and peak video memory;
  - pronunciation, by transcribing the clip back with Whisper and scoring word
    / character error against the text it was given (a mispronounced word or
    letter shows up as an error; Arabic is compared without diacritics, so the
    test checks the WORDS came out right, listen to the saved wav files for
    the vowelling itself).
Cases: plain Arabic, fully diacritized Arabic (tashkeel), numbers, English,
spelled-out letters / codes, and mixed Arabic + English.

SILMA clones the voice of a reference clip. Arabic uses the clip shipped with
the package; English uses a short edge-tts clip made here (or --ref-en).
SILMA is Modern Standard Arabic, not Jordanian dialect, and is a diffusion
model: it makes a whole sentence at a time, it does not stream.

    pip install silma-tts            # also needs ffmpeg
    python test_tts_silma.py --out tts_clips --json tts_results.json
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_asr_voices import wer_cer  # noqa: E402

AR_REF_TEXT = "ويدقق النظر في القرآن الكريم وسائر الكتب السماوية ويتبع مسالك الرسل العظام عليهم الصلاة والسلام."
EN_REF_TEXT = "Good morning, this is a short sample of my voice for the interpreter."
EN_REF_VOICE = "en-AU-WilliamNeural"

CASES = [
    ("ar-plain", "ar", "صباح الخير، هل يمكنك أن ترسل لي الكتالوج بعد الظهر؟"),
    ("ar-tashkeel", "ar", "صَبَاحُ الخَيْرِ، هَلْ يُمْكِنُكَ أَنْ تُرْسِلَ لِي الكَتَالُوجَ بَعْدَ الظُّهْرِ؟"),
    ("ar-numbers", "ar", "تكلف طاولة الطعام 4,850 ديناراً وسنشحنها في 14 آذار."),
    ("ar-long", "ar", "نحتاج إلى تأكيد الشحنة قبل يوم الخميس، وإلا ستفوت الحاوية السفينة، وسيتأخر وصول الأرائك إلى صالة العرض تسعة أيام."),
    ("en-plain", "en", "Good morning, can you send me the catalogue this afternoon?"),
    ("en-numbers", "en", "The walnut dining table costs 4,850 dinars and ships on the 14th of March."),
    ("en-spelled", "en", "Please reserve S K U W D four four seven one B for Layla."),
    ("en-long", "en", "We need to confirm the shipment before Thursday, otherwise the container will miss the vessel and the sofas will arrive nine days late."),
    ("mixed", "ar", "بدنا نعمل campaign على Instagram للـ showroom الجديد"),
]
BASELINE_VOICES = {"en": "en-US-AndrewNeural", "ar": "ar-LB-RamiNeural"}


@dataclass
class Row:
    engine: str
    case: str
    lang: str
    gen_s: float
    audio_s: float
    rtf: float
    vram_mb: float | None
    wer: float
    cer: float
    heard: str
    wav: str
    error: str | None = None


async def edge_clip(text: str, voice: str) -> tuple[np.ndarray, int, float]:
    import edge_tts

    start = time.perf_counter()
    data = bytearray()
    async for chunk in edge_tts.Communicate(text, voice).stream():
        if chunk["type"] == "audio":
            data += chunk["data"]
    took = time.perf_counter() - start
    audio, sr = sf.read(io.BytesIO(bytes(data)), dtype="float32", always_2d=True)
    return audio.mean(axis=1), sr, took


def whisper_model(cpu: bool):
    from faster_whisper import WhisperModel

    if cpu:
        return WhisperModel("small", device="cpu", compute_type="int8")
    return WhisperModel("large-v3-turbo", device="cuda", compute_type="float16")


def transcribe(model, wav: np.ndarray, sr: int, lang: str) -> str:
    if sr != 16000:
        wav = np.interp(np.linspace(0, len(wav) - 1, int(len(wav) * 16000 / sr)), np.arange(len(wav)), wav).astype(np.float32)
    segments, _ = model.transcribe(wav.astype(np.float32), language=lang, beam_size=1)
    return " ".join(s.text.strip() for s in segments)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path("tts_clips"))
    ap.add_argument("--json", type=Path)
    ap.add_argument("--ref-en", type=Path, help="English reference clip for SILMA (default: made with edge-tts)")
    ap.add_argument("--ref-en-text", default=EN_REF_TEXT)
    ap.add_argument("--cpu", action="store_true", help="CPU: small Whisper for the round trip, only 3 cases")
    ap.add_argument("--no-silma", action="store_true", help="only the edge-tts baseline")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    cases = CASES[:1] + CASES[4:5] + CASES[1:2] if args.cpu else CASES

    silma, torch = None, None
    if not args.no_silma:
        try:
            import torch  # noqa: F811
            from silma_tts.api import SilmaTTS

            t0 = time.perf_counter()
            silma = SilmaTTS()
            print(f"SILMA loaded in {time.perf_counter() - t0:.1f}s (cuda: {bool(torch.cuda.is_available())})")
        except Exception as error:
            print(f"SILMA unavailable ({type(error).__name__}: {error}) - baseline only. pip install silma-tts, and install ffmpeg.")
    refs: dict[str, tuple[str, str]] = {}
    if silma is not None:
        import importlib.resources as res

        refs["ar"] = (str(res.files("silma_tts") / "infer" / "ref_audio_samples" / "ar.ref.24k.wav"), AR_REF_TEXT)
        if args.ref_en:
            refs["en"] = (str(args.ref_en), args.ref_en_text)
        else:
            audio, sr, _ = await edge_clip(EN_REF_TEXT, EN_REF_VOICE)
            path = args.out / "ref_en.wav"
            sf.write(path, audio, sr)
            refs["en"] = (str(path), EN_REF_TEXT)

    asr = whisper_model(args.cpu)
    rows: list[Row] = []
    for name, lang, text in cases:
        if silma is not None:
            ref_file, ref_text = refs["en" if lang == "en" else "ar"]
            wav_path = args.out / f"silma-{name}.wav"
            try:
                if torch.cuda.is_available():
                    torch.cuda.reset_peak_memory_stats()
                    torch.cuda.synchronize()
                start = time.perf_counter()
                wav, sr, _ = silma.infer(ref_file=ref_file, ref_text=ref_text, gen_text=text, file_wave=str(wav_path), seed=1, speed=1)
                if torch.cuda.is_available():
                    torch.cuda.synchronize()
                took = time.perf_counter() - start
                wav = np.asarray(wav, dtype=np.float32).reshape(-1)
                heard = transcribe(asr, wav, sr, lang)
                wer, cer = wer_cer(heard, text, lang)
                vram = torch.cuda.max_memory_allocated() / 1e6 if torch.cuda.is_available() else None
                rows.append(Row("silma", name, lang, round(took, 2), round(len(wav) / sr, 2), round(took / (len(wav) / sr), 3), vram and round(vram), round(wer, 3), round(cer, 3), heard, str(wav_path)))
            except Exception as error:
                rows.append(Row("silma", name, lang, 0, 0, 0, None, 1.0, 1.0, "", str(wav_path), f"{type(error).__name__}: {error}"))
        try:
            audio, sr, took = await edge_clip(text, BASELINE_VOICES[lang])
            path = args.out / f"edge-{name}.wav"
            sf.write(path, audio, sr)
            heard = transcribe(asr, audio, sr, lang)
            wer, cer = wer_cer(heard, text, lang)
            rows.append(Row("edge-tts", name, lang, round(took, 2), round(len(audio) / sr, 2), round(took / (len(audio) / sr), 3), None, round(wer, 3), round(cer, 3), heard, str(path)))
        except Exception as error:
            print(f"edge-tts baseline unavailable for {name}: {error}")

    print(f"\n{'engine':<9} {'case':<12} {'gen s':>6} {'audio s':>8} {'RTF':>6} {'VRAM MB':>8} {'WER':>6} {'CER':>6}")
    for r in rows:
        print(f"{r.engine:<9} {r.case:<12} {r.gen_s:>6.2f} {r.audio_s:>8.2f} {r.rtf:>6.2f} {str(r.vram_mb or '-'):>8} {r.wer:>6.0%} {r.cer:>6.0%}" + (f"  ERROR {r.error}" if r.error else ""))
    for engine in sorted({r.engine for r in rows}):
        sub = [r for r in rows if r.engine == engine and not r.error]
        if sub:
            print(f"\n{engine}: mean RTF {statistics.fmean(r.rtf for r in sub):.2f}  median gen {statistics.median(r.gen_s for r in sub):.2f}s  mean WER {statistics.fmean(r.wer for r in sub):.1%}  mean CER {statistics.fmean(r.cer for r in sub):.1%}")
    if args.json:
        args.json.write_text(json.dumps([asdict(r) for r in rows], ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
