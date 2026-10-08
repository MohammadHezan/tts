"""Training data for the Arabic <-> English translation LoRA.

Builds chat examples in EXACTLY the format the meeting bot sends at run time
(engine/app/prompts.py: the same system prompt, the same "Translate from X
into Y" request, the same glossary line), so the adapter learns the job it
will be asked to do - short spoken-style sentences, one direction per
message, translation only, no chatter.

Sources (open parallel text, Hugging Face datasets):
  Helsinki-NLP/opus-100          ar-en   subtitles / everyday speech
  Helsinki-NLP/news_commentary   ar-en   longer, formal sentences
  --extra FILE.jsonl             your own pairs: {"en": "...", "ar": "..."}
                                 per line - meeting transcripts in your
                                 domain are worth far more than either set.
Check each dataset's licence for your use (OPUS-100 and News-Commentary are
listed as "unknown").

    pip install datasets
    python prepare_data.py --out data --per-source 30000 [--extra mine.jsonl]
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))

from app.config import REPO_ROOT  # noqa: E402
from app.glossary import Glossary  # noqa: E402
from app.prompts import build_system_prompt, build_translation_request  # noqa: E402

_AR = re.compile(r"[؀-ۿ]")
_LAT = re.compile(r"[A-Za-z]")


def good_pair(en: str, ar: str) -> bool:
    en, ar = en.strip(), ar.strip()
    ne, na = len(en.split()), len(ar.split())
    if not (2 <= ne <= 60 and 2 <= na <= 60):
        return False
    if not _LAT.search(en) or not _AR.search(ar) or len(_AR.findall(en)) > 0:
        return False  # wrong script on either side = misaligned pair
    if not (0.5 <= ne / na <= 2.2):
        return False  # lengths far apart = misaligned pair
    return True


def load_hf(name: str, config: str, limit: int, seed: int):
    from datasets import load_dataset

    ds = load_dataset(name, config, split="train", streaming=True).shuffle(seed=seed, buffer_size=50_000)
    n = 0
    for row in ds:
        t = row["translation"]
        if good_pair(t["en"], t["ar"]):
            yield t["en"].strip(), t["ar"].strip()
            n += 1
            if n >= limit:
                return


def load_extra(path: Path):
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            if d.get("en") and d.get("ar"):
                yield d["en"].strip(), d["ar"].strip()


def example(system: str, glossary: Glossary, src: str, text: str, ref: str) -> dict:
    tgt = "ar" if src == "en" else "en"
    return {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": build_translation_request(text, src, tgt, glossary)},
            {"role": "assistant", "content": ref},
        ]
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path("data"))
    ap.add_argument("--per-source", type=int, default=30_000)
    ap.add_argument("--extra", type=Path, action="append", default=[], help="JSONL of {en, ar}; upweighted x3")
    ap.add_argument("--eval-size", type=int, default=500)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    system = build_system_prompt("retail_furniture")
    glossary = Glossary.load(REPO_ROOT / "glossary.yaml")

    pairs: list[tuple[str, str]] = []
    for name, config in (("Helsinki-NLP/opus-100", "ar-en"), ("Helsinki-NLP/news_commentary", "ar-en")):
        got = list(load_hf(name, config, args.per_source, args.seed))
        print(f"{name}: {len(got):,} pairs kept")
        pairs += got
    for path in args.extra:
        got = list(load_extra(path))
        print(f"{path}: {len(got):,} pairs (x3)")
        pairs += got * 3

    pairs = list(dict.fromkeys(pairs))  # exact duplicates out
    rng.shuffle(pairs)
    held, train = pairs[: args.eval_size], pairs[args.eval_size :]
    args.out.mkdir(parents=True, exist_ok=True)
    for fname, subset in (("train.jsonl", train), ("eval.jsonl", held)):
        with (args.out / fname).open("w", encoding="utf-8") as f:
            for en, ar in subset:
                # one random direction per pair: the bot flips direction every turn
                src = rng.choice(("en", "ar"))
                text, ref = (en, ar) if src == "en" else (ar, en)
                f.write(json.dumps(example(system, glossary, src, text, ref), ensure_ascii=False) + "\n")
    print(f"wrote {len(train):,} train / {len(held):,} eval examples to {args.out}/")


if __name__ == "__main__":
    main()
