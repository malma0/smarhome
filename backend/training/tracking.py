"""Experiment tracking for the home model (MLflow): every version with what it
was trained on, how training went and how it scored on the exams.

    mlflow ui --backend-store-uri sqlite:///training/mlflow.db     (from backend/, then http://localhost:5000)

One run per trained version in the "jarvis-home" experiment, named like its
Ollama model (jarvis-home-v6): parameters (base, data, LoRA settings), the
loss curve, and exam scores - exam.<suite>.accuracy and exam.<suite>.<kind>
for each kind of task, exam.<suite>.seconds per answer. Suites: main (the 103
cases), brightness, house2, and reruns of main (h8, h23, fixed, lan...).
Scores of the untrained base model go to a run of its own, for comparison.

Kept on this machine (training/mlflow.db, training/mlartifacts/ - gitignored),
like the models. Optional: training and the exam work without mlflow
installed; it's in requirements-train.txt.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path

TRAINING = Path(__file__).resolve().parent
# MLflow's own variable points it elsewhere - a server, or a scratch store for a test
TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI") or f"sqlite:///{(TRAINING / 'mlflow.db').as_posix()}"
ARTIFACTS = Path(os.environ.get("JARVIS_MLFLOW_ARTIFACTS") or TRAINING / "mlartifacts")
EXPERIMENT = "jarvis-home"
REGISTERED_MODEL = "jarvis-home"


def available() -> bool:
    try:
        import mlflow  # noqa: F401
    except ImportError:
        return False
    return True


def setup():
    """mlflow, pointed at this machine's store and the home model's experiment."""
    import mlflow

    mlflow.set_tracking_uri(TRACKING_URI)
    if mlflow.get_experiment_by_name(EXPERIMENT) is None:
        mlflow.create_experiment(EXPERIMENT, artifact_location=ARTIFACTS.as_uri())
    mlflow.set_experiment(EXPERIMENT)
    return mlflow


def exam_metrics(path: Path, suite: str) -> dict[str, float]:
    """A results file -> exam.<suite>.accuracy, .passed, .cases, .seconds and accuracy per kind."""
    rows = [json.loads(line) for line in Path(path).open(encoding="utf-8")]
    if not rows:
        return {}
    by_kind: dict[str, list[bool]] = defaultdict(list)
    for row in rows:
        by_kind[row["kind"]].append(bool(row["ok"]))
    passed = sum(r["ok"] for r in rows)
    metrics = {f"exam.{suite}.accuracy": passed / len(rows), f"exam.{suite}.passed": float(passed),
               f"exam.{suite}.cases": float(len(rows)),
               f"exam.{suite}.seconds": sum(r["seconds"] for r in rows) / len(rows)}
    metrics.update({f"exam.{suite}.{kind}": sum(oks) / len(oks) for kind, oks in by_kind.items()})
    return metrics


def suite_name(spec: str, tag: str = "") -> str:
    """qwen:jarvis-home-v6 + "brightness" -> "brightness"; through Ollama's own
    template (ollama:...) the suite says so: "ollama_template_main"."""
    kind = spec.partition(":")[0]
    suite = tag or "main"
    return suite if kind == "qwen" else f"{kind}_template_{suite}"


def model_name(spec: str) -> str:
    return spec.partition(":")[2]


def run_for_model(mlflow, model: str):
    """The run of this Ollama model (tag ollama_model), the newest if several."""
    runs = mlflow.search_runs(experiment_names=[EXPERIMENT], filter_string=f"tags.ollama_model = '{model}'",
                              order_by=["start_time DESC"], max_results=1, output_format="list")
    return runs[0] if runs else None


def log_exam(spec: str, tag: str, results: Path, suite: str | None = None) -> str | None:
    """An exam's scores into its model's run (one is created for a model that
    has none - the base model, a cloud one). Returns the run id."""
    if not available():
        return None
    mlflow = setup()
    model = model_name(spec)
    metrics = exam_metrics(results, suite or suite_name(spec, tag))
    if not metrics:
        return None
    run = run_for_model(mlflow, model)
    with mlflow.start_run(run_id=run.info.run_id if run else None,
                          run_name=None if run else model,
                          tags=None if run else {"ollama_model": model, "kind": "baseline"}) as active:
        mlflow.log_metrics(metrics)
        mlflow.log_artifact(str(results), artifact_path="exams")
        return active.info.run_id
