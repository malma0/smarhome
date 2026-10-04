"""Load test of the model service: the exam's phrases sent to /v1/command at
a few concurrency levels - latency percentiles, throughput and errors as the
caller sees them, saved as JSON and into the served version's MLflow run
(serving.c<N>.* metrics).

    python -m model_service.loadtest                        (200 requests at 1, 2 and 4 at once)
    python -m model_service.loadtest --requests 100 --concurrency 1 4 8

One RTX 2060 runs one generation at a time: more at once raises throughput
only while Ollama can batch, then it's queueing - which is what the p95 shows.
"""

import argparse
import asyncio
import json
import random
import statistics
import time
from datetime import datetime
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parents[1]
CASES = [BACKEND / "training" / "data" / f for f in ("eval.jsonl", "eval_brightness.jsonl", "eval_house2.jsonl")]
RESULTS = Path(__file__).resolve().parent / "results"


def phrases() -> list[str]:
    said = [json.loads(line)["said"] for path in CASES if path.exists() for line in path.open(encoding="utf-8")]
    random.Random(0).shuffle(said)
    return said


def summarize(latencies: list[float], errors: int, wall: float) -> dict:
    ordered = sorted(latencies)

    def at(p: float) -> float:
        return round(ordered[min(len(ordered) - 1, int(p * len(ordered)))] * 1000, 1) if ordered else 0.0

    return {"requests": len(latencies) + errors, "errors": errors,
            "error_rate": round(errors / max(1, len(latencies) + errors), 4),
            "p50_ms": at(0.50), "p90_ms": at(0.90), "p95_ms": at(0.95), "p99_ms": at(0.99),
            "mean_ms": round(statistics.fmean(ordered) * 1000, 1) if ordered else 0.0,
            "throughput_rps": round(len(latencies) / wall, 2) if wall else 0.0, "wall_s": round(wall, 1)}


async def level(client: httpx.AsyncClient, url: str, texts: list[str], concurrency: int) -> dict:
    queue: asyncio.Queue[str] = asyncio.Queue()
    for text in texts:
        queue.put_nowait(text)
    latencies: list[float] = []
    errors = 0

    async def worker() -> None:
        nonlocal errors
        while not queue.empty():
            text = queue.get_nowait()
            started = time.perf_counter()
            try:
                response = await client.post(f"{url}/v1/command", json={"text": text})
                response.raise_for_status()
                latencies.append(time.perf_counter() - started)
            except httpx.HTTPError:
                errors += 1

    started = time.perf_counter()
    await asyncio.gather(*(worker() for _ in range(concurrency)))
    return summarize(latencies, errors, time.perf_counter() - started)


async def run(url: str, requests: int, concurrency: list[int], warmup: int) -> dict:
    texts = phrases()
    async with httpx.AsyncClient(timeout=180, trust_env=False) as client:
        model = (await client.get(f"{url}/v1/model")).json()
        for text in texts[:warmup]:  # the first call may load the model - not what's being measured
            await client.post(f"{url}/v1/command", json={"text": text})
        report = {"model": model.get("model"), "registry_version": model.get("version"),
                  "quantization": model.get("quantization"), "when": datetime.now().isoformat(timespec="seconds"),
                  "levels": {}}
        for c in concurrency:
            sample = [texts[(i + warmup) % len(texts)] for i in range(requests)]
            report["levels"][str(c)] = result = await level(client, url, sample, c)
            print(f"concurrency {c}: p50 {result['p50_ms']} ms, p95 {result['p95_ms']} ms, "
                  f"p99 {result['p99_ms']} ms, {result['throughput_rps']} req/s, errors {result['errors']}")
        report["server_stats"] = (await client.get(f"{url}/v1/stats")).json()
    return report


def save(report: dict) -> Path:
    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / f"loadtest_{report['model']}_{report['when'][:10]}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def log_to_mlflow(report: dict, path: Path) -> str | None:
    from training import tracking

    if not tracking.available():
        return None
    mlflow = tracking.setup()
    run = tracking.run_for_model(mlflow, report["model"])
    if run is None:
        return None
    with mlflow.start_run(run_id=run.info.run_id):
        for c, result in report["levels"].items():
            mlflow.log_metrics({f"serving.c{c}.{k}": v for k, v in result.items() if isinstance(v, (int, float))})
        mlflow.log_artifact(str(path), artifact_path="serving")
    return run.info.run_id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8090")
    parser.add_argument("--requests", type=int, default=200, help="per concurrency level")
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 2, 4])
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--no-mlflow", action="store_true")
    args = parser.parse_args()
    report = asyncio.run(run(args.url.rstrip("/"), args.requests, args.concurrency, args.warmup))
    path = save(report)
    print(f"saved {path}")
    if not args.no_mlflow:
        run_id = log_to_mlflow(report, path)
        if run_id:
            print(f"MLflow: serving metrics in run {run_id[:8]}")


if __name__ == "__main__":
    main()
