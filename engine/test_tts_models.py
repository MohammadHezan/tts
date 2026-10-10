"""Compares text-to-speech models on the same sentences: how long until the
audio exists, how much video memory it takes, and - by reading each clip back
through the speech model - how much of what it says is wrong.

Two steps, because each voice model wants its own Python environment:

  generate (inside that model's environment):
    python test_tts_models.py generate --engine chatterbox --out ../results/tts/chatterbox
    engines: edge (the current voices), chatterbox, kokoro (English only), piper (Arabic only)

  score (the main environment, with the speech model):
    python test_tts_models.py score --asr C:/path/to/cohere-transcribe-03-2026 ../results/tts/edge ../results/tts/chatterbox

The clips are written as WAV files next to timing.json: listen to them, the
read-back error rate can't judge how natural a voice sounds.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

SENTENCES = [
    ("en", "en-0", "Good morning, can you send me the catalogue this afternoon?"),
    ("en", "en-1", "The walnut dining table costs four thousand eight hundred and fifty dinars."),
    ("en", "en-2", "We need to confirm the shipment before Thursday, otherwise the container will miss the vessel."),
    ("en", "en-3", "Ahmad from Hamilton and Co wants the Chesterfield sofa in the Aurora finish."),
    ("ar", "ar-0", "صباح الخير، هل يمكنك أن ترسل لي الكتالوج بعد الظهر؟"),
    ("ar", "ar-1", "تكلف طاولة الطعام من خشب الجوز أربعة آلاف وثمانمئة وخمسين ديناراً."),
    ("ar", "ar-2", "نحتاج إلى تأكيد الشحنة قبل يوم الخميس، وإلا ستفوت الحاوية السفينة."),
    ("ar", "ar-3", "يريد أحمد من شركة هاملتون أريكة تشيسترفيلد بتشطيب أورورا."),
]


def gpu_used_mb() -> int | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True, check=True).stdout
        return int(out.split()[0])
    except Exception:
        return None


# ----------------------------------------------------------------------- engines
# Each returns synth(text, lang) -> (float32 mono samples, sample rate).


def engine_edge():
    import edge_tts

    voices = {"en": "en-US-AndrewNeural", "ar": "ar-LB-RamiNeural"}
    rates = {"en": "-10%", "ar": "-5%"}

    async def one(text: str, lang: str):
        for attempt in range(5):
            try:
                data = bytearray()
                async for chunk in edge_tts.Communicate(text, voices[lang], rate=rates[lang]).stream():
                    if chunk["type"] == "audio":
                        data += chunk["data"]
                audio, sr = sf.read(io.BytesIO(bytes(data)), dtype="float32", always_2d=True)
                return audio.mean(axis=1), sr
            except Exception:
                time.sleep(3 * (attempt + 1))
        raise RuntimeError("edge-tts kept failing")

    return lambda text, lang: asyncio.run(one(text, lang))


def engine_chatterbox():
    import torch
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    model = ChatterboxMultilingualTTS.from_pretrained(device="cuda")

    def synth(text: str, lang: str):
        with torch.inference_mode():
            wav = model.generate(text, language_id=lang)
        return wav.squeeze().cpu().numpy().astype(np.float32), model.sr

    return synth


def engine_kokoro():
    import os

    from kokoro_onnx import Kokoro

    if os.environ.get("TTS_GPU"):
        os.environ["ONNX_PROVIDER"] = "CUDAExecutionProvider"
    model_dir = Path(__file__).resolve().parents[1] / "models"
    kokoro = Kokoro(str(model_dir / "kokoro-v1.0.onnx"), str(model_dir / "voices-v1.0.bin"))

    def synth(text: str, lang: str):
        if lang != "en":
            raise NotImplementedError("Kokoro has no Arabic voice")
        samples, sr = kokoro.create(text, voice="af_heart", speed=0.9, lang="en-us")
        return samples.astype(np.float32), sr

    return synth


def engine_piper():
    from piper import PiperVoice, SynthesisConfig

    import os

    voice = PiperVoice.load(str(Path(__file__).resolve().parents[1] / "models" / "ar_JO-kareem-medium.onnx"), use_cuda=bool(os.environ.get("TTS_GPU")))

    def synth(text: str, lang: str):
        if lang != "ar":
            raise NotImplementedError("this Piper voice is Arabic")
        chunks = list(voice.synthesize(text, syn_config=SynthesisConfig(length_scale=1.05)))
        pcm = np.frombuffer(b"".join(c.audio_int16_bytes for c in chunks), dtype=np.int16)
        return pcm.astype(np.float32) / 32768.0, chunks[0].sample_rate

    return synth


ENGINES = {"edge": engine_edge, "chatterbox": engine_chatterbox, "kokoro": engine_kokoro, "piper": engine_piper}


def generate(args) -> int:
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    before = gpu_used_mb()
    synth = ENGINES[args.engine]()
    loaded = gpu_used_mb()
    rows = []
    # one throw-away sentence per language first: the first call pays for warm-up
    for lang in ("en", "ar"):
        try:
            synth("Hello." if lang == "en" else "مرحبا.", lang)
        except NotImplementedError:
            pass
        except Exception as error:
            print(f"warm-up {lang}: {error!r}")
    peak = gpu_used_mb()
    for lang, ident, text in SENTENCES:
        start = time.perf_counter()
        try:
            audio, sr = synth(text, lang)
        except NotImplementedError:
            continue
        except Exception as error:
            print(f"{ident}: failed {error!r}")
            continue
        took = time.perf_counter() - start
        sf.write(out / f"{ident}.wav", audio, sr)
        audio_s = len(audio) / sr
        rows.append({"id": ident, "lang": lang, "text": text, "gen_s": round(took, 3), "audio_s": round(audio_s, 2), "rtf": round(took / audio_s, 3)})
        print(f"{ident}: {took:.2f}s for {audio_s:.1f}s of audio (RTF {took / audio_s:.2f})")
        peak = max(peak or 0, gpu_used_mb() or 0)
    summary = {"engine": args.engine, "rows": rows, "vram_loaded_mb": None if None in (before, loaded) else loaded - before,
               "vram_peak_mb": None if None in (before, peak) else peak - before}
    (out / "timing.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


# ------------------------------------------------------------------------- score


def score(args) -> int:
    sys.path.insert(0, str(Path(__file__).parent))
    from test_asr_voices import build_asr, wer_cer

    asr = build_asr(f"cohere:{args.asr}", cpu=False)

    async def transcribe(audio16: np.ndarray, lang: str) -> str:
        asr.start_utterance("t")
        hyp = await asr.finalize((np.clip(audio16, -1, 1) * 32767).astype(np.int16))
        return hyp.text

    table = []
    for folder in args.dirs:
        timing = json.loads((folder / "timing.json").read_text(encoding="utf-8"))
        per_lang: dict[str, list] = {"en": [], "ar": []}
        for row in timing["rows"]:
            audio, sr = sf.read(str(folder / f"{row['id']}.wav"), dtype="float32", always_2d=True)
            mono = audio.mean(axis=1)
            if sr != 16000:
                mono = np.interp(np.linspace(0, len(mono) - 1, int(len(mono) * 16000 / sr)), np.arange(len(mono)), mono)
            hyp = asyncio.run(transcribe(mono, row["lang"]))
            w, c = wer_cer(hyp, row["text"], row["lang"])
            per_lang[row["lang"]].append((w, c, row["rtf"], row["gen_s"]))
            print(f"{timing['engine']:>11} {row['id']}  WER {w:5.1%}  said: {hyp}")
        for lang, items in per_lang.items():
            if items:
                table.append((timing["engine"], lang, statistics.mean(i[0] for i in items), statistics.mean(i[1] for i in items),
                              statistics.median(i[3] for i in items), statistics.mean(i[2] for i in items), timing.get("vram_peak_mb")))
    print("\nengine       lang   WER     CER    seconds/sentence   RTF    extra VRAM (MB)")
    for e, lang, w, c, gen, rtf, vram in table:
        print(f"{e:<12} {lang:<4} {w:6.1%} {c:6.1%} {gen:12.2f}      {rtf:6.2f}   {vram}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--engine", choices=sorted(ENGINES), required=True)
    g.add_argument("--out", type=Path, required=True)
    s = sub.add_parser("score")
    s.add_argument("--asr", required=True, help="folder of Cohere Transcribe 03-2026")
    s.add_argument("dirs", nargs="+", type=Path)
    args = ap.parse_args()
    return generate(args) if args.cmd == "generate" else score(args)


if __name__ == "__main__":
    sys.exit(main())
