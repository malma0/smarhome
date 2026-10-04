"""Puts the versions trained before MLflow (v1-v6) into it, from what they
left on this machine: the loss curve and runtime from the last checkpoint's
trainer_state.json, the adapter, the exam results - and registers each as a
version of the "jarvis-home" model. Run once; running again replaces nothing,
it skips versions already there.

    python -m training.mlflow_backfill
    python -m training.mlflow_backfill --production jarvis-home-v6
"""

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

from training import tracking
from training.train_lora import BASE

RUNS = tracking.TRAINING / "runs"
RESULTS = tracking.TRAINING / "results"

COMMON = {"base": BASE, "method": "QLoRA", "quantization": "nf4, double quant, fp16 compute",
          "lora_rank": 16, "lora_alpha": 32, "lora_dropout": 0.05,
          "lora_targets": "q,k,v,o,gate,up,down", "epochs": 2, "learning_rate": 2e-4, "lr_schedule": "cosine",
          "warmup_ratio": 0.03, "batch_size": 1, "grad_accumulation": 8, "optimizer": "paged_adamw_8bit",
          "max_len": 3072, "gpu": "RTX 2060 6 GB", "gguf": "q8_0"}

# What each version was: its folder, the training data (dialogue count, the commit that made it) and
# why it was trained - from the repository's history.
VERSIONS = [
    {"model": "jarvis-home", "folder": "home-1.5b", "dialogues": 1995, "data_commit": "5e3619a",
     "data": "train.jsonl", "tools": "app/llm/home_model_tools.json",
     "note": "First fine-tune: 1995 dialogues from 588 intents."},
    {"model": "jarvis-home-v2", "folder": "home-1.5b-v2", "dialogues": 2653, "data_commit": "29c8fb3",
     "data": "train.jsonl", "tools": "app/llm/home_model_tools.json",
     "note": "All 800 intents; a bare 'главный кран' is water, gas only when named."},
    {"model": "jarvis-home-v3", "folder": "home-1.5b-v3", "dialogues": 2653, "data_commit": "89ddfeb",
     "data": "train.jsonl", "tools": "app/llm/home_model_tools.json",
     "note": "The wake word back in 65% of phrases, as live transcripts keep it."},
    {"model": "jarvis-home-v4", "folder": "home-1.5b-v4", "dialogues": 3135, "data_commit": "f45a26a",
     "data": "train.jsonl + train_brightness.jsonl", "tools": "app/llm/home_model_tools.json",
     "note": "Brightness: 'потемнее/посветлее', a 30% step from what the house says."},
    {"model": "jarvis-home-v5", "folder": "home-1.5b-v5", "dialogues": 4144, "data_commit": "3fdce51",
     "data": "train.jsonl + train_brightness.jsonl + train_house2.jsonl", "tools": "training/data/tools.json (v5)",
     "note": "The grown house: six tools - curtains, humidifiers, the guard, history, schedules."},
    {"model": "jarvis-home-v6", "folder": "home-v6", "dialogues": 4144, "data_commit": "c5c1dfc",
     "data": "train.jsonl + train_brightness.jsonl + train_house2.jsonl", "tools": "training/data/tools.json",
     "note": "History by named periods (today, last_night, this_week...) - dates worked out in code. "
             "Resumed from the epoch-1 checkpoint: the first epoch ran out of VRAM into shared memory.",
     "log": "train_resume.log",
     # its first epoch's process was stopped before it printed a runtime: START in status.log
     # (04:11:15) to checkpoint-518 written (14:48:25)
     "extra_seconds": 38230},
]
LOGS = {"home-1.5b": RUNS / "home-1.5b.log"}  # v1's log sits next to its folder


def runtime_from_log(v: dict) -> tuple[float, float] | None:
    """(seconds, train loss) from the "train_runtime" line Trainer prints at the end - it comes after the
    last checkpoint, so trainer_state.json doesn't have it."""
    path = LOGS.get(v["folder"], RUNS / v["folder"] / v.get("log", "train.log"))
    if not path.exists():
        return None
    text = path.read_text("utf-8", errors="replace")
    runtime = re.findall(r"'train_runtime': ([0-9.]+)", text)
    loss = re.findall(r"'train_loss': ([0-9.]+)", text)
    if not runtime:
        return None
    if "extra_seconds" in v:  # resumed: Trainer's train_loss covers only the steps after the resume
        state = json.loads((last_checkpoint(RUNS / v["folder"]) / "trainer_state.json").read_text("utf-8"))
        losses = [e["loss"] for e in state["log_history"] if "loss" in e]
        return float(runtime[-1]) + v["extra_seconds"], sum(losses) / len(losses)
    return float(runtime[-1]), float(loss[-1]) if loss else float("nan")

BASELINE = "qwen2.5:1.5b"  # the untrained base model - its own run, for comparison


def last_checkpoint(folder: Path) -> Path:
    return max(folder.glob("checkpoint-*"), key=lambda p: int(p.name.split("-")[1]))


def results_for(model: str) -> dict[str, Path]:
    """suite -> results file for this model."""
    found = {}
    for path in RESULTS.glob("*.jsonl"):
        kind, _, rest = path.stem.partition("_")
        for suffix in ("",) + tuple(f"_{t}" for t in ("brightness", "house2", "h8", "h23", "fixed", "lan", "t0")):
            if rest == model.replace(":", "_") + suffix:
                found[tracking.suite_name(f"{kind}:{model}", suffix[1:])] = path
    return found


def backfill(production: str | None) -> None:
    mlflow = tracking.setup()
    client = mlflow.MlflowClient()
    if not _registered(client):
        client.create_registered_model(tracking.REGISTERED_MODEL,
                                       description="Qwen2.5-1.5B-Instruct fine-tuned (QLoRA) on Jarvis's home "
                                                   "conversations, served by Ollama as GGUF q8_0.")
    for v in VERSIONS:
        existing = tracking.run_for_model(mlflow, v["model"])
        if existing is not None:
            timing = runtime_from_log(v)
            if timing and "train_runtime_hours" not in existing.data.metrics:  # an earlier backfill missed it
                client.log_metric(existing.info.run_id, "train_runtime_hours", timing[0] / 3600)
                client.log_metric(existing.info.run_id, "train_loss", timing[1])
                print(f"{v['model']}: training time added")
            else:
                print(f"{v['model']}: already there")
            continue
        folder = RUNS / v["folder"]
        state = json.loads((last_checkpoint(folder) / "trainer_state.json").read_text("utf-8"))
        started = datetime.fromtimestamp(min(p.stat().st_mtime for p in folder.glob("checkpoint-*")))
        with mlflow.start_run(run_name=v["model"], tags={"ollama_model": v["model"], "kind": "fine-tune",
                                                         "backfilled": "from trainer_state.json and exam results",
                                                         "mlflow.note.content": v["note"],
                                                         "trained_around": started.strftime("%Y-%m-%d")}) as run:
            mlflow.log_params({**COMMON, "dialogues": v["dialogues"], "data": v["data"],
                               "data_commit": v["data_commit"], "tools": v["tools"],
                               "steps": state["global_step"]})
            for entry in state["log_history"]:
                if "loss" in entry:
                    mlflow.log_metrics({"loss": entry["loss"], "grad_norm": entry["grad_norm"],
                                        "learning_rate": entry["learning_rate"]}, step=entry["step"])
            timing = runtime_from_log(v)
            if timing:
                mlflow.log_metrics({"train_runtime_hours": timing[0] / 3600, "train_loss": timing[1]})
            for suite, path in results_for(v["model"]).items():
                mlflow.log_metrics(tracking.exam_metrics(path, suite))
                mlflow.log_artifact(str(path), artifact_path="exams")
            mlflow.log_artifacts(str(folder / "adapter"), artifact_path="adapter")
            mlflow.log_artifact(str(folder / "Modelfile"))
            version = client.create_model_version(tracking.REGISTERED_MODEL,
                                                  source=f"{run.info.artifact_uri}/adapter", run_id=run.info.run_id,
                                                  tags={"ollama_model": v["model"]}, description=v["note"])
            print(f"{v['model']}: run {run.info.run_id[:8]}, registered as version {version.version}")
    if tracking.run_for_model(mlflow, BASELINE) is None:
        for suite, path in results_for(BASELINE).items():
            tracking.log_exam(f"{path.stem.partition('_')[0]}:{BASELINE}", "", path, suite=suite)
        print(f"{BASELINE}: baseline run")
    if production:
        version = next(mv for mv in client.search_model_versions(f"name='{tracking.REGISTERED_MODEL}'")
                       if mv.tags.get("ollama_model") == production)
        client.set_registered_model_alias(tracking.REGISTERED_MODEL, "production", version.version)
        print(f"production -> {production} (version {version.version})")


def _registered(client) -> bool:
    try:
        client.get_registered_model(tracking.REGISTERED_MODEL)
    except Exception:  # noqa: BLE001 - mlflow's RestException: not there yet
        return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--production", help="the version Jarvis serves now, e.g. jarvis-home-v6")
    backfill(parser.parse_args().production)


if __name__ == "__main__":
    main()
