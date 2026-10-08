"""Shrink a Gemma 3 GGUF for English + Arabic only.

  1. Vocabulary pruning. Gemma's 262k-token vocabulary covers 140+ languages;
     its embedding table is also the output layer (tied weights), so every
     token costs memory AND a row of the matrix multiply that produces each
     generated word. Here only tokens made of Latin, Arabic, digits,
     punctuation, symbols/emoji and the model's control/byte tokens are kept.
     Because a kept token's pieces are themselves kept (they use the same
     characters), the tokenizer still splits English and Arabic text exactly
     as before. Rows of the (quantized) embedding table are copied byte for
     byte - nothing is requantized, no weight value changes.

  2. Depth pruning (optional, --drop-layers). Drops whole groups of 6
     transformer layers (5 sliding-window + 1 global) from the middle, which
     keeps llama.cpp's 5:1 attention pattern aligned. WITHOUT the LoRA
     "healing" fine-tune the quality loss can be large - measure it
     (engine/test_translator_model.py) before using it.

Context length and KV-cache size are runtime settings, not part of the file
(translator.ollama.num_ctx, translator.context_turns in the config).

    python deploy/prune_model.py IN.gguf OUT.gguf [--drop-layers 12-17]

Then register it with Ollama (deploy/prune_model.py prints the commands).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from gguf import GGUFReader, GGUFValueType, GGUFWriter

SPACE = "▁"  # SentencePiece's visible space

# Unicode ranges a token may be made of.
_RANGES = [
    (0x0009, 0x000D),  # tab, newline - the chat template is built from them
    (0x0020, 0x007E),  # ASCII
    (0x00A0, 0x024F),  # Latin-1, Latin Extended A/B (names: é, ü, ñ ...)
    (0x02B0, 0x02FF),  # modifier letters
    (0x0300, 0x036F),  # combining marks
    (0x0600, 0x06FF),  # Arabic
    (0x0750, 0x077F),  # Arabic Supplement
    (0x08A0, 0x08FF),  # Arabic Extended-A
    (0x200B, 0x206F),  # general punctuation, zero-width joiners
    (0x20A0, 0x20CF),  # currency
    (0x2100, 0x218F),  # letterlike (℃ №), number forms
    (0x2190, 0x21FF),  # arrows
    (0x2200, 0x22FF),  # math
    (0x2500, 0x25FF),  # box / shapes
    (0x2600, 0x27BF),  # misc symbols, dingbats
    (0x2B00, 0x2BFF),
    (0x2581, 0x2581),  # SPACE
    (0xFB50, 0xFDFF),  # Arabic presentation forms A
    (0xFE00, 0xFE0F),  # variation selectors
    (0xFE70, 0xFEFF),  # Arabic presentation forms B
    (0x1F300, 0x1FAFF),  # emoji
]
_CONTROL_KEEP = {"<pad>", "<eos>", "<bos>", "<unk>", "<mask>", "<start_of_turn>", "<end_of_turn>"}

TOKEN_NORMAL, TOKEN_UNKNOWN, TOKEN_CONTROL, TOKEN_USER, TOKEN_UNUSED, TOKEN_BYTE = 1, 2, 3, 4, 5, 6


def _allowed(token: str) -> bool:
    return all(any(lo <= ord(c) <= hi for lo, hi in _RANGES) for c in token)


# Optional (--latin-frequent-only): token ids run roughly from most to least
# frequent, and far down the Latin-script tokens are mostly other languages'
# words, code and names. Past the cut-off a token is only kept if it is an
# English dictionary word (or 1-2 characters, the building blocks any word can
# be spelled from). Arabic tokens are always kept. By default nothing Latin is
# dropped, so English is tokenized exactly as before.
FREQUENT_IDS: int | None = None  # --latin-frequent-only N sets it; None keeps every Latin-script token


def load_words() -> set[str]:
    for path in ("/usr/share/dict/american-english", "/usr/share/dict/words"):
        if Path(path).is_file():
            return {w.strip().lower() for w in Path(path).read_text(encoding="utf-8", errors="ignore").splitlines() if w.strip()}
    return set()


def keep_token(token: str, ttype: int, tid: int = 0, words: frozenset[str] | set[str] = frozenset()) -> bool:
    if ttype == TOKEN_BYTE:
        return True  # byte fallback for anything unusual
    if ttype in (TOKEN_CONTROL, TOKEN_USER, TOKEN_UNKNOWN):
        return token in _CONTROL_KEEP or token == "<unk>"
    if ttype == TOKEN_UNUSED:
        return False
    if not _allowed(token):
        return False
    bare = token.replace(SPACE, "")
    if FREQUENT_IDS is None or tid < FREQUENT_IDS or len(bare) <= 2 or any("\u0600" <= c <= "\u06ff" or c > "\u00ff" for c in bare):
        return True
    return bare.lower() in words  # a whole English word


def parse_layers(spec: str | None) -> set[int]:
    drop: set[int] = set()
    for part in (spec or "").split(","):
        if part.strip():
            lo, _, hi = part.partition("-")
            drop.update(range(int(lo), int(hi or lo) + 1))
    if drop and (len(drop) % 6 or min(drop) % 6):
        sys.exit("--drop-layers must cover whole groups of 6 starting at a multiple of 6 (e.g. 12-17) to keep the 5:1 attention pattern")
    return drop


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", type=Path)
    ap.add_argument("dst", type=Path)
    ap.add_argument("--latin-frequent-only", type=int, metavar="N", help="also drop Latin tokens past id N unless they are English words (aggressive: changes how rare words are split; try 50000)")
    ap.add_argument("--drop-layers", help="e.g. 12-17 (a multiple of 6 layers, starting at a multiple of 6)")
    args = ap.parse_args()
    drop = parse_layers(args.drop_layers)
    global FREQUENT_IDS
    FREQUENT_IDS = args.latin_frequent_only

    r = GGUFReader(str(args.src))
    fields = r.fields
    tokens = fields["tokenizer.ggml.tokens"].contents()
    scores = [float(x) for x in fields["tokenizer.ggml.scores"].contents()]
    types = [int(x) for x in fields["tokenizer.ggml.token_type"].contents()]
    embd = next(t for t in r.tensors if t.name == "token_embd.weight")
    rows = embd.data.shape[0]  # tokens that actually have an embedding row

    words = load_words()
    if not words:
        print('no /usr/share/dict wordlist: keeping only the most frequent Latin tokens', file=sys.stderr)
    keep = [i for i in range(min(rows, len(tokens))) if keep_token(tokens[i], types[i], i, words)]
    new_id = {old: new for new, old in enumerate(keep)}
    print(f"vocabulary: {len(tokens):,} -> {len(keep):,} tokens ({len(keep) / rows:.0%})", file=sys.stderr)

    w = GGUFWriter(str(args.dst), "gemma3")
    skip = {"GGUF.version", "GGUF.tensor_count", "GGUF.kv_count", "general.architecture"}
    id_keys = {"tokenizer.ggml.bos_token_id", "tokenizer.ggml.eos_token_id", "tokenizer.ggml.padding_token_id", "tokenizer.ggml.unknown_token_id"}
    for key, f in fields.items():
        if key in skip or key.startswith("tokenizer.ggml.tokens") or key in ("tokenizer.ggml.scores", "tokenizer.ggml.token_type"):
            continue
        if key == "tokenizer.ggml.merges":
            kept = {tokens[i] for i in keep}
            merges = [m for m in f.contents() if all(p in kept for p in m.split(" ")) and m.replace(" ", "") in kept]
            w.add_array(key, merges)
            print(f"merges: {len(f.contents()):,} -> {len(merges):,}", file=sys.stderr)
            continue
        if key == "gemma3.block_count":
            w.add_uint32(key, f.contents() - len(drop))
            continue
        if key in id_keys:
            w.add_uint32(key, new_id[f.contents()])
            continue
        vtype = f.types[0]
        if vtype == GGUFValueType.ARRAY:
            w.add_array(key, f.contents())
        else:
            w.add_key_value(key, f.contents(), vtype)
    w.add_token_list([tokens[i] for i in keep])
    w.add_token_scores([scores[i] for i in keep])
    w.add_token_types([types[i] for i in keep])

    layer_map: dict[int, int] = {}
    for old in range(int(fields["gemma3.block_count"].contents())):
        if old not in drop:
            layer_map[old] = len(layer_map)
    for t in r.tensors:
        name, data = t.name, t.data
        if name == "token_embd.weight":
            data = data[np.array(keep)]
        elif name.startswith("blk."):
            _, idx, rest = name.split(".", 2)
            if int(idx) in drop:
                continue
            name = f"blk.{layer_map[int(idx)]}.{rest}"
        w.add_tensor(name, data, raw_shape=data.shape, raw_dtype=t.tensor_type)

    w.write_header_to_file()
    w.write_kv_data_to_file()
    w.write_tensors_to_file(progress=False)
    w.close()
    mb = args.dst.stat().st_size / 1e6
    print(f"wrote {args.dst} ({mb:,.0f} MB, was {args.src.stat().st_size / 1e6:,.0f} MB)", file=sys.stderr)
    print(
        "\nRegister it:\n"
        f"  ollama show --modelfile <the original model> > Modelfile   # then change its FROM line to {args.dst.resolve()}\n"
        "  ollama create <new-name> -f Modelfile",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
