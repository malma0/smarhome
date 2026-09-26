"""Folds a trained LoRA adapter into the base model - a plain model folder
that llama.cpp converts to GGUF for Ollama.

    python -m training.merge_adapter --adapter training/runs/home-1.5b/adapter --out training/runs/home-1.5b/merged
"""

import argparse

from training.train_lora import BASE


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--base", default=BASE)
    args = parser.parse_args()

    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    model = AutoModelForCausalLM.from_pretrained(args.base, torch_dtype=torch.float16)
    model = PeftModel.from_pretrained(model, args.adapter).merge_and_unload()
    model.save_pretrained(args.out, safe_serialization=True)
    AutoTokenizer.from_pretrained(args.adapter).save_pretrained(args.out)
    print(f"merged model saved to {args.out}")


if __name__ == "__main__":
    main()
