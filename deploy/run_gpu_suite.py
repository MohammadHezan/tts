"""Run every model test in one go and write one summary - made for the PC with
the NVIDIA GPU.

    python deploy/run_gpu_suite.py                  # the full suite (needs a GPU)
    python deploy/run_gpu_suite.py --cpu-smoke      # tiny run on any machine, to check the plumbing
    python deploy/run_gpu_suite.py --dry-run        # print the commands only

Steps (each logged, each writing JSON into results/gpu_suite_<time>/):
  1. translator  engine/test_translator_model.py  Gemma 3 4B vs the old 12B (--require-gpu)
  2. asr         engine/test_asr_voices.py        Whisper large-v3-turbo, the Cohere dialect
                 model and - if you pass --cohere-dir (a downloaded copy) - Cohere Transcribe 03-2026
  3. tts         engine/test_tts_silma.py         SILMA TTS vs the edge-tts voices
then SUMMARY.md with the tables. A step that fails is recorded and the rest still run.

Gated Cohere repos: accept the terms on huggingface.co with your account, download
the model yourself, and pass the folder with --cohere-dir. Nothing here handles
tokens or accepts licences for you.
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
NEW = "hf.co/innerloop-dev/gemma3-4b-text:Q4_K_M"
OLD = "hf.co/unsloth/gemma-3-12b-it-GGUF:IQ4_XS"
COHERE_DIALECT = "cohere:oddadmix/cohere-transcribe-arabic-07-2026-dialectal-v2@Newmetrics/cohere-transcribe-arabic-07-2026"
PY = sys.executable


def gpu_info() -> list[str]:
    if not shutil.which("nvidia-smi"):
        return []
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"], capture_output=True, text=True, timeout=15, check=True).stdout
    except (subprocess.SubprocessError, OSError):
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def run(name: str, cmd: list[str], out: Path, dry: bool, cwd: Path = ENGINE) -> dict:
    print(f"\n### {name}\n$ {' '.join(map(str, cmd))}")
    if dry:
        return {"name": name, "status": "dry-run"}
    log = out / f"{name}.log"
    start = time.time()
    with log.open("w", encoding="utf-8") as f:
        proc = subprocess.run(cmd, cwd=cwd, stdout=f, stderr=subprocess.STDOUT, text=True)
    print(f"  exit {proc.returncode} in {time.time() - start:.0f}s -> {log}")
    return {"name": name, "status": "ok" if proc.returncode == 0 else f"failed (exit {proc.returncode}, see {log.name})", "seconds": round(time.time() - start)}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def md_translator(data) -> str:
    if not data:
        return "_no translator results_\n"
    lines = ["| model | pass | score | chrF | first token p50 | total p50 | total p95 | tok/s | in VRAM |", "|---|---|---|---|---|---|---|---|---|"]
    place = data.get("placement", {})
    for model, s in data["summaries"].items():
        share = (place.get(model) or {}).get("vram_share")
        lines.append(f"| {model} | {s['pass_rate']:.0%} | {s['mean_score']:.3f} | {s['mean_chrf']:.3f} | {s['ttft_p50_ms']:.0f} ms | {s['lat_p50_ms']:.0f} ms | {s['lat_p95_ms']:.0f} ms | {s['tok_per_s']} | {'-' if share is None else f'{share:.0%}'} |")
    return "\n".join(lines) + "\n"


def md_asr(rows) -> str:
    if not rows:
        return "_no ASR results_\n"
    out = []
    for model in dict.fromkeys(r["model"] for r in rows):
        rs = [r for r in rows if r["model"] == model]
        out.append(f"**{model}** - WER {statistics.fmean(r['wer'] for r in rs):.1%}, CER {statistics.fmean(r['cer'] for r in rs):.1%}, language right {sum(r['lang_ok'] for r in rs) / len(rs):.0%}, median {statistics.median(r['ms'] for r in rs):.0f} ms\n")
        by = defaultdict(list)
        for r in rs:
            by[r["condition"]].append(r)
        out += ["| condition | WER | CER | median ms |", "|---|---|---|---|"]
        out += [f"| {c} | {statistics.fmean(x['wer'] for x in v):.1%} | {statistics.fmean(x['cer'] for x in v):.1%} | {statistics.median(x['ms'] for x in v):.0f} |" for c, v in by.items()]
        out.append("")
    return "\n".join(out) + "\n"


def md_tts(rows) -> str:
    if not rows:
        return "_no TTS results_\n"
    lines = ["| engine | case | gen s | audio s | RTF | VRAM MB | WER | CER |", "|---|---|---|---|---|---|---|---|"]
    lines += [f"| {r['engine']} | {r['case']} | {r['gen_s']} | {r['audio_s']} | {r['rtf']} | {r['vram_mb'] or '-'} | {r['wer']:.0%} | {r['cer']:.0%} |" for r in rows]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--cpu-smoke", action="store_true", help="tiny subset, no GPU needed - checks the scripts and the summary")
    ap.add_argument("--skip-pull", action="store_true", help="don't `ollama pull` the models")
    ap.add_argument("--cohere-dir", type=Path, help="folder with a downloaded cohere-transcribe-03-2026")
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    gpus = gpu_info()
    print("GPU:", "; ".join(gpus) if gpus else "none")
    if not gpus and not args.cpu_smoke and not args.dry_run:
        print("No NVIDIA GPU found (nvidia-smi). Run this on the GPU PC, or use --cpu-smoke for a plumbing check.")
        return 2
    out = ROOT / "results" / time.strftime("gpu_suite_%Y%m%d_%H%M%S")
    if not args.dry_run:
        out.mkdir(parents=True)
    smoke = args.cpu_smoke
    steps = []

    if not args.skip_pull and not smoke:
        for model in (NEW, OLD):
            steps.append(run(f"pull-{model.split('/')[-1][:20]}", ["ollama", "pull", model], out, args.dry_run))
    t_json = out / "translator.json"
    t_cmd = [PY, "test_translator_model.py", "--model", NEW, "--json", str(t_json), "--runs", "1" if smoke else str(args.runs)]
    t_cmd += ["--only", "tiny,short"] if smoke else ["--require-gpu", "--baseline", OLD]
    if smoke:
        t_cmd += ["--baseline", "llama3.2:1b"]
    steps.append(run("translator", t_cmd, out, args.dry_run))

    a_json = out / "asr.json"
    a_cmd = [PY, "test_asr_voices.py", "--json", str(a_json)]
    if smoke:
        a_cmd += ["--model", "whisper:small", "--cpu", "--voices-per-lang", "1", "--conditions", "clean,very quiet", "--cache", str(out / "asr_clips")]
    else:
        a_cmd += ["--model", "whisper:large-v3-turbo", "--model", COHERE_DIALECT, "--cache", str(ROOT / "asr_test_clips")]
        if args.cohere_dir:
            a_cmd += ["--model", f"cohere:{args.cohere_dir}"]
    steps.append(run("asr", a_cmd, out, args.dry_run))

    s_json = out / "tts.json"
    s_cmd = [PY, "test_tts_silma.py", "--json", str(s_json), "--out", str(out / "tts_clips")] + (["--cpu"] if smoke else [])
    steps.append(run("tts", s_cmd, out, args.dry_run))

    if args.dry_run:
        return 0
    summary = [f"# GPU suite {time.strftime('%Y-%m-%d %H:%M')}\n", f"GPU: {'; '.join(gpus) if gpus else 'none (CPU smoke run - numbers are not GPU numbers)'}\n"]
    if args.cohere_dir is None and not smoke:
        summary.append("_Cohere Transcribe 03-2026 not tested: pass --cohere-dir once you have downloaded it (gated repo)._\n")
    summary.append("## Steps\n" + "\n".join(f"- {s['name']}: {s['status']}" + (f" ({s['seconds']}s)" if 'seconds' in s else "") for s in steps) + "\n")
    summary += ["## Translation\n", md_translator(load(t_json)), "## Speech recognition\n", md_asr(load(a_json)), "## Text to speech (SILMA vs edge-tts)\n", md_tts(load(s_json))]
    (out / "SUMMARY.md").write_text("\n".join(summary), encoding="utf-8")
    print(f"\nWrote {out / 'SUMMARY.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
