"""Train the Arabic <-> English LoRA adapter for Gemma 3 4B (QLoRA, one GPU).

Needs an NVIDIA GPU (a 12 GB card is enough: the base is loaded in 4-bit,
only the adapter - ~60 MB at rank 16 - is trained). Not runnable on CPU.

    pip install torch transformers peft bitsandbytes accelerate datasets
    python prepare_data.py --out data
    python train_lora.py --data data/train.jsonl --out adapter

Trains on the language model only (the base is the text weights of
google/gemma-3-4b-it; the vision tower is never touched), on the answer
tokens only. The adapter targets attention and MLP projections, not the
embedding table, so it also fits a vocabulary-pruned GGUF.
Then: ./export_adapter.sh
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, Trainer, TrainingArguments

BASE = "unsloth/gemma-3-4b-it"  # ungated mirror of google/gemma-3-4b-it
# language model only: skips the SigLIP vision tower and the projector
TARGETS = r".*language_model.*\.(q_proj|k_proj|v_proj|o_proj|gate_proj|up_proj|down_proj)"


def encode(tok, messages: list[dict], max_len: int) -> dict:
    prompt = tok.apply_chat_template(messages[:-1], tokenize=False, add_generation_prompt=True)
    answer = messages[-1]["content"] + "<end_of_turn>\n"
    p_ids = tok(prompt, add_special_tokens=False)["input_ids"]
    a_ids = tok(answer, add_special_tokens=False)["input_ids"]
    ids = (p_ids + a_ids)[:max_len]
    labels = ([-100] * len(p_ids) + a_ids)[:max_len]  # loss on the translation only
    return {"input_ids": ids, "labels": labels}


class Rows(torch.utils.data.Dataset):
    def __init__(self, rows):
        self.rows = rows

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, i):
        return self.rows[i]


def collate(pad_id: int):
    def fn(batch):
        n = max(len(b["input_ids"]) for b in batch)
        pad = lambda seq, v: seq + [v] * (n - len(seq))  # noqa: E731
        return {
            "input_ids": torch.tensor([pad(b["input_ids"], pad_id) for b in batch]),
            "attention_mask": torch.tensor([pad([1] * len(b["input_ids"]), 0) for b in batch]),
            "labels": torch.tensor([pad(b["labels"], -100) for b in batch]),
        }

    return fn


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=Path("data/train.jsonl"))
    ap.add_argument("--out", type=Path, default=Path("adapter"))
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--epochs", type=float, default=1.0)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--accum", type=int, default=4)
    ap.add_argument("--max-len", type=int, default=768)
    args = ap.parse_args()

    tok = AutoTokenizer.from_pretrained(BASE)
    model = AutoModelForCausalLM.from_pretrained(
        BASE,
        quantization_config=BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True
        ),
        torch_dtype=torch.bfloat16,
        attn_implementation="eager",  # recommended for Gemma 3 training
        device_map={"": 0},
    )
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    model = get_peft_model(
        model,
        LoraConfig(r=args.rank, lora_alpha=2 * args.rank, lora_dropout=0.05, target_modules=TARGETS, task_type="CAUSAL_LM"),
    )
    model.print_trainable_parameters()

    rows = [encode(tok, json.loads(line)["messages"], args.max_len) for line in args.data.read_text(encoding="utf-8").splitlines() if line.strip()]
    Trainer(
        model=model,
        args=TrainingArguments(
            output_dir=str(args.out / "checkpoints"),
            per_device_train_batch_size=args.batch,
            gradient_accumulation_steps=args.accum,
            num_train_epochs=args.epochs,
            learning_rate=args.lr,
            lr_scheduler_type="cosine",
            warmup_ratio=0.03,
            bf16=True,
            logging_steps=20,
            save_steps=500,
            save_total_limit=2,
            report_to="none",
            remove_unused_columns=False,
        ),
        train_dataset=Rows(rows),
        data_collator=collate(tok.pad_token_id),
    ).train()
    model.save_pretrained(args.out)
    tok.save_pretrained(args.out)
    print(f"adapter saved to {args.out}/")


if __name__ == "__main__":
    main()
