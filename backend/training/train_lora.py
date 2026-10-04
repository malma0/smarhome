"""Fine-tunes a small open model on Jarvis's home conversations (LoRA over a
4-bit base - QLoRA). Made for the PC with the RTX 2060, 6 GB.

    python -m training.train_lora --data training/data/train.jsonl --out training/runs/home-1.5b
    python -m training.train_lora --data training/data/pilot.jsonl --out training/runs/smoke --max-steps 20

Stopped (the PC turned off, Ctrl+C)? The same command again goes on from the
last checkpoint in --out - one is kept every SAVE_STEPS steps (~an hour on the 2060).

Base: Qwen2.5-1.5B-Instruct - Apache 2.0 (the 3B isn't: its license limits
commercial use), good at Russian, small enough to train and serve on 6 GB.
Only Jarvis's own turns are learned - its tool calls and what it says; the
person's words and the house's results are context, not targets. The
conversations are rendered with the model's own chat template (tools and
tool calls included), the same one Ollama uses to serve it afterwards.
The RTX 2060 (Turing) has no bfloat16 - float16 throughout.
"""

import argparse
import json
import random
from pathlib import Path

from training import tracking

BASE = "Qwen/Qwen2.5-1.5B-Instruct"
START, END = "<|im_start|>assistant\n", "<|im_end|>"
SAVE_STEPS = 50  # of ~1000 for the full set: a stop loses an hour at most, not the night


def to_template(messages: list[dict]) -> list[dict]:
    """Our records -> what the Qwen2.5 chat template expects."""
    out = []
    for m in messages:
        m = dict(m)
        if m["role"] == "assistant" and m.get("tool_calls"):
            m["content"] = m.get("content") or ""
        out.append(m)
    return out


def encode(tokenizer, record: dict, max_len: int) -> dict | None:
    text = tokenizer.apply_chat_template(to_template(record["messages"]), tools=record["tools"], tokenize=False)
    spans, pos = [], 0
    while (i := text.find(START, pos)) != -1:  # Jarvis's turns: the only thing the loss looks at
        start = i + len(START)
        end = text.find(END, start)
        end = len(text) if end == -1 else end + len(END)
        spans.append((start, end))
        pos = end
    enc = tokenizer(text, return_offsets_mapping=True, add_special_tokens=False)
    if len(enc["input_ids"]) > max_len:
        return None  # rather drop than cut a conversation's end (its answer)
    labels = [tok if any(a < e and b > s for s, e in spans) else -100
              for tok, (a, b) in zip(enc["input_ids"], enc["offset_mapping"])]
    return {"input_ids": enc["input_ids"], "labels": labels}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", nargs="+", default=["training/data/train.jsonl"])
    parser.add_argument("--out", default="training/runs/home-1.5b")
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--epochs", type=float, default=2)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--max-len", type=int, default=3072)
    parser.add_argument("--max-steps", type=int, default=-1)
    parser.add_argument("--rank", type=int, default=16)
    parser.add_argument("--model-name", help="its Ollama name, for MLflow (default from --out: runs/home-v7 -> "
                                             "jarvis-home-v7)")
    parser.add_argument("--no-mlflow", action="store_true", help="don't record this run in MLflow")
    args = parser.parse_args()
    model_name = args.model_name or "jarvis-" + Path(args.out).name.replace("home-1.5b", "home")
    track = not args.no_mlflow and tracking.available()

    import torch
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, Trainer,
                              TrainingArguments)

    tokenizer = AutoTokenizer.from_pretrained(args.base)
    records = [json.loads(line) for path in args.data for line in Path(path).open(encoding="utf-8")]
    random.Random(0).shuffle(records)
    examples = [e for r in records if (e := encode(tokenizer, r, args.max_len))]
    print(f"{len(examples)} of {len(records)} conversations fit in {args.max_len} tokens; "
          f"longest {max(len(e['input_ids']) for e in examples)}")

    model = AutoModelForCausalLM.from_pretrained(
        args.base,
        quantization_config=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                               bnb_4bit_compute_dtype=torch.float16, bnb_4bit_use_double_quant=True),
        torch_dtype=torch.float16,
        device_map={"": 0},
    )
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    model = get_peft_model(model, LoraConfig(
        r=args.rank, lora_alpha=2 * args.rank, lora_dropout=0.05, task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    ))
    model.print_trainable_parameters()

    def collate(batch):
        width = max(len(b["input_ids"]) for b in batch)
        pad = tokenizer.pad_token_id
        return {
            "input_ids": torch.tensor([b["input_ids"] + [pad] * (width - len(b["input_ids"])) for b in batch]),
            "labels": torch.tensor([b["labels"] + [-100] * (width - len(b["labels"])) for b in batch]),
            "attention_mask": torch.tensor([[1] * len(b["input_ids"]) + [0] * (width - len(b["input_ids"]))
                                            for b in batch]),
        }

    trainer = Trainer(
        model=model,
        train_dataset=examples,
        data_collator=collate,
        args=TrainingArguments(
            output_dir=args.out, per_device_train_batch_size=1, gradient_accumulation_steps=8,
            num_train_epochs=args.epochs, max_steps=args.max_steps, learning_rate=args.lr,
            lr_scheduler_type="cosine", warmup_ratio=0.03, fp16=True, logging_steps=10,
            save_strategy="steps", save_steps=SAVE_STEPS, save_total_limit=2,
            optim="paged_adamw_8bit", gradient_checkpointing=True,
            report_to=["mlflow"] if track else [], remove_unused_columns=False,
        ),
    )
    from transformers.trainer_utils import get_last_checkpoint

    last = get_last_checkpoint(args.out) if Path(args.out).is_dir() else None
    if last:
        print(f"going on from {last}")
    run = start_tracking(model_name, args, len(examples), resuming=bool(last)) if track else None
    trainer.train(resume_from_checkpoint=last)  # Trainer's own MLflow callback logs the loss into the run
    model.save_pretrained(Path(args.out) / "adapter")
    tokenizer.save_pretrained(Path(args.out) / "adapter")
    print(f"adapter saved to {Path(args.out) / 'adapter'}")
    if run is not None:
        finish_tracking(run, Path(args.out) / "adapter", model_name)


def start_tracking(model_name: str, args, examples: int, resuming: bool):
    """This version's MLflow run - the one it already has when training goes on from a checkpoint."""
    import subprocess

    mlflow = tracking.setup()
    run = tracking.run_for_model(mlflow, model_name) if resuming else None
    try:
        data_commit = subprocess.run(["git", "log", "-1", "--format=%h", "--", *args.data],
                                     capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        data_commit = "unknown"
    active = mlflow.start_run(run_id=run.info.run_id if run else None, run_name=None if run else model_name,
                              tags=None if run else {"ollama_model": model_name, "kind": "fine-tune"})
    if run is None:
        mlflow.log_params({"base": args.base, "method": "QLoRA", "quantization": "nf4, double quant, fp16 compute",
                           "lora_rank": args.rank, "lora_alpha": 2 * args.rank, "lora_dropout": 0.05,
                           "max_len": args.max_len, "dialogues": examples, "data": " + ".join(args.data),
                           "data_commit": data_commit, "gpu": "RTX 2060 6 GB"})
    return active


def finish_tracking(run, adapter: Path, model_name: str) -> None:
    """The adapter into the run and the model registry, as a new version of jarvis-home."""
    import mlflow

    mlflow.log_artifacts(str(adapter), artifact_path="adapter")
    client = mlflow.MlflowClient()
    try:
        client.get_registered_model(tracking.REGISTERED_MODEL)
    except Exception:  # noqa: BLE001 - mlflow's RestException: not there yet
        client.create_registered_model(tracking.REGISTERED_MODEL)
    version = client.create_model_version(tracking.REGISTERED_MODEL, source=f"{run.info.artifact_uri}/adapter",
                                          run_id=run.info.run_id, tags={"ollama_model": model_name})
    mlflow.end_run()
    print(f"MLflow: run {run.info.run_id[:8]}, {tracking.REGISTERED_MODEL} version {version.version}")


if __name__ == "__main__":
    main()
