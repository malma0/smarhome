import json

import pytest

from training import tracking


def _results(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return path


def test_exam_metrics_score_the_whole_exam_and_each_kind(tmp_path):
    path = _results(tmp_path / "r.jsonl", [
        {"kind": "control", "ok": True, "seconds": 0.5},
        {"kind": "control", "ok": False, "seconds": 0.7},
        {"kind": "chat", "ok": True, "seconds": 0.3},
        {"kind": "history", "ok": True, "seconds": 0.5},
    ])
    metrics = tracking.exam_metrics(path, "house2")
    assert metrics["exam.house2.accuracy"] == 0.75
    assert metrics["exam.house2.passed"] == 3 and metrics["exam.house2.cases"] == 4
    assert metrics["exam.house2.control"] == 0.5 and metrics["exam.house2.chat"] == 1.0
    assert metrics["exam.house2.seconds"] == pytest.approx(0.5)


def test_suites_and_model_names_from_the_exams_spec():
    assert tracking.suite_name("qwen:jarvis-home-v6") == "main"
    assert tracking.suite_name("qwen:jarvis-home-v6", "brightness") == "brightness"
    assert tracking.suite_name("ollama:jarvis-home", "t0") == "ollama_template_t0"  # Ollama's own chat template
    assert tracking.model_name("qwen:qwen2.5:1.5b") == "qwen2.5:1.5b"


def test_an_exam_goes_into_its_models_run_and_a_model_without_one_gets_a_baseline_run(tmp_path, monkeypatch):
    pytest.importorskip("mlflow")
    monkeypatch.setattr(tracking, "TRACKING_URI", f"sqlite:///{(tmp_path / 'mlflow.db').as_posix()}")
    monkeypatch.setattr(tracking, "ARTIFACTS", tmp_path / "artifacts")
    mlflow = tracking.setup()
    with mlflow.start_run(run_name="jarvis-home-v7", tags={"ollama_model": "jarvis-home-v7"}) as trained:
        pass
    results = _results(tmp_path / "qwen_jarvis-home-v7_brightness.jsonl",
                       [{"kind": "brightness", "ok": True, "seconds": 0.8}])

    assert tracking.log_exam("qwen:jarvis-home-v7", "brightness", results) == trained.info.run_id
    run = mlflow.get_run(trained.info.run_id)
    assert run.data.metrics["exam.brightness.accuracy"] == 1.0

    base_run = tracking.log_exam("qwen:qwen2.5:1.5b", "", results)  # the untrained base model has no run yet
    assert base_run != trained.info.run_id
    assert mlflow.get_run(base_run).data.tags["kind"] == "baseline"
