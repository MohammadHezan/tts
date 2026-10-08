"""Trim Cohere Transcribe (cohere_asr) down to English and Arabic.

What this CAN remove: the tokenizer's vocabulary. The model writes its
transcript with a 16,384-piece SentencePiece vocabulary, and a language tag
token (<|fr|>, <|zh|> ...) exists for each language it was trained on. This
script drops
  - the language tags of every language except --keep-langs (default en,ar)
  - every text piece that is not Latin / Arabic / digit / punctuation script
and cuts the matching rows of the decoder's embedding table and output head,
renumbering the tokens that remain (ids in config / generation_config are
remapped, tokenizer.model and the added-token table are rewritten).

What it can NOT remove: the languages themselves. They live in the shared
48-layer encoder and the decoder layers (~98% of the 2.07B parameters), which
serve all languages at once - there is no per-language slice to cut. Expect
the files to shrink by only ~1-2%. The ways to actually make this model
lighter are int8/4-bit weights (asr.cohere.quantize: int8 already gives 2.4 GB)
or training a smaller model, not pruning tokens.

Dialects are not a component either: the model has language tags (ar, en), no
dialect tags. Jordanian Arabic and Australian English are handled by the same
weights; the Jordanian result comes from fine-tuning (see the dialect model in
config), not from something that can be kept or removed here.

    python deploy/prune_asr.py SRC_DIR DST_DIR --inspect     # look first, change nothing
    python deploy/prune_asr.py SRC_DIR DST_DIR               # prune
    python engine/test_asr_voices.py --model cohere:SRC_DIR --model cohere:DST_DIR   # compare

SRC_DIR is a downloaded copy of the repo (config.json, tokenizer.model,
model.safetensors ...). Needs: pip install safetensors sentencepiece protobuf
torch (for bf16 tensors).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

# Structural tokens that look like language tags (<|xx|>) but are not.
STRUCTURAL = {"pnc", "nopnc", "itn", "noitn", "unklang", "nospeech", "startofcontext", "spkchange", "audioseparator",
              "timestamp", "notimestamp", "diarize", "nodiarize", "startoftranscript", "endoftext"}
_TAG = re.compile(r"^<\|([^|]+)\|>$")
_RANGES = [
    (0x0009, 0x000D), (0x0020, 0x007E), (0x00A0, 0x024F), (0x0300, 0x036F), (0x0600, 0x06FF), (0x0750, 0x077F),
    (0x08A0, 0x08FF), (0x200B, 0x206F), (0x20A0, 0x20CF), (0x2581, 0x2581), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF),
]
NORMAL, UNKNOWN, CONTROL, USER_DEFINED, UNUSED, BYTE = 1, 2, 3, 4, 5, 6


def is_language_tag(piece: str) -> str | None:
    m = _TAG.match(piece)
    if not m:
        return None
    name = m.group(1)
    if name in STRUCTURAL or name.startswith("emo:"):
        return None
    return name


def text_ok(piece: str) -> bool:
    return all(any(lo <= ord(c) <= hi for lo, hi in _RANGES) for c in piece)


def decide_keep(pieces, keep_langs: set[str], protected_ids: set[int]) -> list[int]:
    keep = []
    for i, p in enumerate(pieces):
        tag = is_language_tag(p.piece)
        if i in protected_ids or p.type in (UNKNOWN, BYTE):
            ok = True
        elif tag is not None:
            ok = tag in keep_langs
        elif p.type in (CONTROL, USER_DEFINED) or _TAG.match(p.piece) or (p.piece.startswith("<") and p.piece.endswith(">")):
            ok = True  # every other special token (<|pnc|>, <|emo:happy|>, <pad> ...) stays
        elif p.type == UNUSED:
            ok = False
        else:
            ok = text_ok(p.piece)
        if ok:
            keep.append(i)
    return keep


def remap_ids(obj, mapping: dict[int, int], path: str = ""):
    """Every integer stored under a key containing 'token_id' goes through `mapping`."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if "token_id" in k and isinstance(v, int):
                if v not in mapping:
                    sys.exit(f"{path}{k}={v} points at a token that was pruned - refusing to continue")
                out[k] = mapping[v]
            elif "token_id" in k and isinstance(v, list):
                out[k] = [mapping[x] for x in v]
            else:
                out[k] = remap_ids(v, mapping, f"{path}{k}.")
        return out
    if isinstance(obj, list):
        return [remap_ids(x, mapping, path) for x in obj]
    return obj


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", type=Path)
    ap.add_argument("dst", type=Path)
    ap.add_argument("--keep-langs", default="en,ar", help="language tags to keep (default en,ar)")
    ap.add_argument("--inspect", action="store_true", help="only report what would change")
    args = ap.parse_args()
    keep_langs = {x.strip() for x in args.keep_langs.split(",") if x.strip()}

    from sentencepiece import SentencePieceProcessor
    from sentencepiece import sentencepiece_model_pb2 as pb

    proto = pb.ModelProto()
    proto.ParseFromString((args.src / "tokenizer.model").read_bytes())
    pieces = list(proto.pieces)
    old_vocab = len(pieces)

    gen = json.loads((args.src / "generation_config.json").read_text()) if (args.src / "generation_config.json").exists() else {}
    protected = {v for k, v in gen.items() if "token_id" in k and isinstance(v, int)}
    keep = decide_keep(pieces, keep_langs, protected)
    mapping = {old: new for new, old in enumerate(keep)}
    dropped_tags = sorted(t for p in pieces if (t := is_language_tag(p.piece)) and t not in keep_langs)
    kept_tags = sorted(t for i in keep if (t := is_language_tag(pieces[i].piece)))
    print(f"vocabulary: {old_vocab:,} -> {len(keep):,} pieces")
    print(f"language tags kept {kept_tags}; dropped {len(dropped_tags)}")
    missing = keep_langs - set(kept_tags)
    if missing:
        sys.exit(f"language tag(s) {sorted(missing)} not found in this tokenizer")

    # which tensors carry a vocabulary dimension
    from safetensors import safe_open

    weights = args.src / "model.safetensors"
    vocab_tensors: list[tuple[str, int]] = []
    with safe_open(weights, framework="pt") as f:
        for name in f.keys():
            shape = f.get_slice(name).get_shape()
            dims = [d for d, n in enumerate(shape) if n == old_vocab]
            if len(dims) > 1:
                sys.exit(f"{name} {shape}: more than one dimension of size {old_vocab}, cannot tell which is the vocabulary")
            if dims:
                vocab_tensors.append((name, dims[0]))
    print("tensors with a vocabulary dimension:")
    for name, dim in vocab_tensors:
        print(f"  {name} (dim {dim})")
    if not vocab_tensors:
        sys.exit("no tensor has a vocabulary-sized dimension - this is not the layout the script expects")
    if args.inspect:
        print("\n--inspect: nothing written")
        return

    args.dst.mkdir(parents=True, exist_ok=True)
    for item in args.src.iterdir():  # code, preprocessor, normalizer ... unchanged
        if item.is_file() and item.name not in {"model.safetensors", "tokenizer.model", "tokenizer.json", "generation_config.json",
                                                 "config.json", "tokenizer_config.json", "special_tokens_map.json"}:
            shutil.copy2(item, args.dst / item.name)

    # --- tokenizer.model
    new_proto = pb.ModelProto()
    new_proto.CopyFrom(proto)
    del new_proto.pieces[:]
    new_proto.pieces.extend(pieces[i] for i in keep)
    (args.dst / "tokenizer.model").write_bytes(new_proto.SerializeToString())
    # tokenizer.json (a fast-tokenizer copy of the same vocabulary) would now disagree; the model's own tokenizer class reads tokenizer.model

    # --- added tokens / special tokens (strings, remapped by content)
    kept_text = {pieces[i].piece for i in keep}
    cfg_path = args.src / "tokenizer_config.json"
    if cfg_path.exists():
        tc = json.loads(cfg_path.read_text())
        added = tc.get("added_tokens_decoder", {})
        tc["added_tokens_decoder"] = {
            str(mapping[int(i)]): v for i, v in added.items() if int(i) in mapping and v.get("content") in kept_text | {v.get("content")}
        }
        if "additional_special_tokens" in tc:
            tc["additional_special_tokens"] = [t for t in tc["additional_special_tokens"] if t in kept_text]
        (args.dst / "tokenizer_config.json").write_text(json.dumps(tc, ensure_ascii=False, indent=2))
    sm_path = args.src / "special_tokens_map.json"
    if sm_path.exists():
        sm = json.loads(sm_path.read_text())
        sm["additional_special_tokens"] = [t for t in sm.get("additional_special_tokens", []) if t in kept_text]
        (args.dst / "special_tokens_map.json").write_text(json.dumps(sm, ensure_ascii=False, indent=2))

    # --- config / generation_config
    def fix_vocab_size(o):
        if isinstance(o, dict):
            return {k: (len(keep) if k in ("vocab_size", "num_classes") and v == old_vocab else fix_vocab_size(v)) for k, v in o.items()}
        if isinstance(o, list):
            return [fix_vocab_size(x) for x in o]
        return o

    config = remap_ids(fix_vocab_size(json.loads((args.src / "config.json").read_text())), mapping)
    (args.dst / "config.json").write_text(json.dumps(config, indent=2))
    if gen:
        (args.dst / "generation_config.json").write_text(json.dumps(remap_ids(gen, mapping), indent=2))

    # --- weights
    import torch
    from safetensors.torch import save_file

    index = torch.tensor(keep)
    by_name = dict(vocab_tensors)
    out = {}
    with safe_open(weights, framework="pt") as f:
        metadata = f.metadata() or {}
        for name in f.keys():
            t = f.get_tensor(name)
            out[name] = t.index_select(by_name[name], index).contiguous() if name in by_name else t.contiguous()
    save_file(out, args.dst / "model.safetensors", metadata={**metadata, "format": "pt"})
    before, after = weights.stat().st_size, (args.dst / "model.safetensors").stat().st_size
    print(f"model.safetensors: {before / 1e6:,.0f} MB -> {after / 1e6:,.0f} MB ({1 - after / before:.1%} smaller)")

    # --- verify: the same text must tokenize to the same pieces before and after
    old_sp, new_sp = SentencePieceProcessor(), SentencePieceProcessor()
    old_sp.LoadFromSerializedProto(proto.SerializeToString())
    new_sp.LoadFromSerializedProto(new_proto.SerializeToString())
    samples = ["Good morning, the walnut table costs 4,850 dollars.", "صباح الخير، تكلف الطاولة 4850 ديناراً أردنياً.",
               "G'day mate, can you send the catalogue?", "شو رأيك نأجل الاجتماع لبكرا؟"]
    bad = 0
    for s in samples:
        a = [pieces[i].piece for i in old_sp.encode(s)]
        b = [new_proto.pieces[i].piece for i in new_sp.encode(s)]
        if a != b:
            bad += 1
            print(f"  tokenization changed for {s!r}:\n    before {a}\n    after  {b}")
    print("tokenizer check:", "identical on all samples" if not bad else f"{bad} sample(s) differ - rare words now split differently")
    print(f"\nwrote {args.dst}. Compare before using it:\n  python engine/test_asr_voices.py --model cohere:{args.src} --model cohere:{args.dst}")


if __name__ == "__main__":
    main()
