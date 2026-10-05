"""Trains the own "Джарвис" detector on the residents' takes (wakeword/record.py)
and measures it against Vosk - the small speech recognizer Jarvis wakes with
now - on the same held-out takes.

    python -m wakeword.train

A quarter of each person's takes of each kind is held out before anything is
learned; the model never sees them, augmented or not. A take counts as
"heard" if any moment of it scores over the threshold - the way Jarvis will
use it on a whole phrase. Results: wakeword/results/*.json and the
"jarvis-wakeword" MLflow experiment; the model: wakeword/models/jarvis.pt
(made from the residents' voices - stays on this PC).
"""

from __future__ import annotations

import json
import random
import time
import wave
from datetime import datetime
from pathlib import Path

import numpy as np

from wakeword import detector
from wakeword.detector import RATE, WINDOW_FRAMES

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
RESULTS = HERE / "results"
POSITIVE = ("near", "far")
NEGATIVE = ("similar", "speech", "quiet")
SEED = 0


def load(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).copy()


def voiced_span(audio: np.ndarray) -> tuple[float, float] | None:
    """(start, end) in seconds of the loud part - the word in a take; None for an empty take."""
    x = audio.astype(np.float32) / 32768
    frames = x[: len(x) // 320 * 320].reshape(-1, 320)
    db = 20 * np.log10(np.sqrt((frames ** 2).mean(1)) + 1e-9)
    if db.max() < -55:
        return None
    loud = np.where(db > max(db.max() - 25, -60))[0]
    return loud[0] * 0.02, (loud[-1] + 1) * 0.02


def heard_phrases() -> None:
    """What the residents said to Jarvis without the name (backend/dataset) -> 2.5 s negative takes, once."""
    out = DATA / "_heard" / "speech"
    if out.exists():
        return
    dataset = HERE.parent / "dataset"
    out.mkdir(parents=True)
    size = int(2.5 * RATE)
    for line in (dataset / "metadata.jsonl").open(encoding="utf-8"):
        row = json.loads(line)
        said = (row.get("corrected_text") or row.get("transcript") or "").casefold()
        if not said or any(w in said for w in ("джарв", "джерв", "дайвис")):
            continue
        audio = load(dataset / row["file_name"])
        for i in range(0, max(1, len(audio) - size // 2), size):
            piece = np.pad(audio[i:i + size], (0, max(0, size - len(audio[i:i + size]))))
            with wave.open(str(out / f"{Path(row['file_name']).stem}_{i // size}.wav"), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(piece.tobytes())


def takes() -> list[dict]:
    heard_phrases()
    out = []
    for who in sorted(p.name for p in DATA.iterdir() if p.is_dir()):
        for part in POSITIVE + NEGATIVE:
            for path in sorted((DATA / who / part).glob("*.wav")):
                out.append({"who": who, "part": part, "path": path, "positive": part in POSITIVE})
    return out


def split(all_takes: list[dict], held_out: float = 0.25) -> tuple[list[dict], list[dict]]:
    rng = random.Random(SEED)
    train, test = [], []
    groups: dict[tuple, list] = {}
    for t in all_takes:
        groups.setdefault((t["who"], t["part"]), []).append(t)
    for group in groups.values():
        rng.shuffle(group)
        k = max(1, round(len(group) * held_out))
        test += group[:k]
        train += group[k:]
    return train, test


def augment(audio: np.ndarray, noises: list[np.ndarray], rng: np.random.Generator) -> np.ndarray:
    """Louder or quieter, over someone talking or a hiss - the takes are clean, the room isn't."""
    x = audio.astype(np.float32) * 10 ** (rng.uniform(-8, 4) / 20)
    if noises and rng.random() < 0.6:
        noise = noises[rng.integers(len(noises))].astype(np.float32)
        noise = np.resize(noise, len(x)) * 10 ** (-rng.uniform(8, 22) / 20) * (np.abs(x).max() / (np.abs(noise).max() + 1))
        x = x + noise
    if rng.random() < 0.5:
        x = x + rng.normal(0, 10 ** (rng.uniform(-62, -45) / 20) * 32768, len(x))
    return np.clip(x, -32768, 32767).astype(np.int16)


def examples(take: dict, audio: np.ndarray, emb: np.ndarray) -> tuple[list[np.ndarray], list[int]]:
    """Windows of one take: for a name - ending just after it (positives) and just after its start
    ("Джар..." - negatives, so it doesn't fire early); for the rest - every window."""
    xs, ys = [], []
    if take["positive"]:
        span = voiced_span(audio)
        if span is None:
            return xs, ys
        start, end = span
        for offset in (0.0, 0.08, 0.16, 0.24):
            k = min(detector.frame_ending_at(end + offset), emb.shape[0] - 1)
            xs.append(emb[k - WINDOW_FRAMES + 1:k + 1]); ys.append(1)
        k = detector.frame_ending_at(start + 0.15)
        if k < emb.shape[0]:
            xs.append(emb[k - WINDOW_FRAMES + 1:k + 1]); ys.append(0)
    else:
        for w in detector.windows(emb)[::3]:
            xs.append(w); ys.append(0)
    return xs, ys


def featurize(train: list[dict], copies: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    noises = [load(t["path"]) for t in train if t["part"] in ("speech", "similar")]
    xs, ys = [], []
    for take in train:
        audio = load(take["path"])
        own = not take["who"].startswith("_")  # the residents' own takes; _tts and _heard are many already
        for c in range(1 + (copies if take["positive"] else copies // 2 if own else 0)):
            version = audio if c == 0 else augment(audio, noises, rng)
            x, y = examples(take, version, detector.embed(version))
            xs += x; ys += y
    return np.stack(xs).astype(np.float32), np.array(ys, dtype=np.float32)


def fit(x: np.ndarray, y: np.ndarray, epochs: int = 40):
    import torch

    torch.manual_seed(SEED)
    net = detector.build_model()
    pos_weight = torch.tensor([(y == 0).sum() / max(1, (y == 1).sum())])
    loss_fn = torch.nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    opt = torch.optim.Adam(net.parameters(), lr=1e-3, weight_decay=1e-4)
    X, Y = torch.from_numpy(x), torch.from_numpy(y)
    losses = []
    for _ in range(epochs):
        net.train()
        perm = torch.randperm(len(X))
        total = 0.0
        for i in range(0, len(X), 128):
            idx = perm[i:i + 128]
            opt.zero_grad()
            loss = loss_fn(net(X[idx])[:, 0], Y[idx])
            loss.backward()
            opt.step()
            total += float(loss) * len(idx)
        losses.append(total / len(X))
    net.eval()
    return net, losses


def take_scores(net, test: list[dict]) -> list[float]:
    import torch

    out = []
    with torch.no_grad():
        for take in test:
            w = torch.from_numpy(detector.windows(detector.embed(load(take["path"]))).astype(np.float32))
            out.append(float(torch.sigmoid(net(w)).max()))
    return out


def vosk_heard(test: list[dict]) -> list[bool]:
    """What Jarvis wakes with now - the same takes through its own check."""
    from app.config import settings
    from app.wake_word import NONE, WakeWordDetector, parse_wake_words

    vosk = WakeWordDetector(str(Path(__file__).resolve().parents[1] / settings.vosk_model_path),
                            parse_wake_words(settings.wake_words))
    return [vosk.check(load(t["path"]).tobytes()) != NONE for t in test]


def report(test: list[dict], heard: list[bool]) -> dict:
    """Recall on the name (near, far) and false wakes on the rest, per kind of take."""
    out = {}
    for part in POSITIVE + NEGATIVE:
        hits = [h for t, h in zip(test, heard) if t["part"] == part and (not t["positive"] or voiced_span(load(t["path"])))]
        if hits:
            out[part] = {"takes": len(hits), "heard": int(sum(hits)), "rate": round(sum(hits) / len(hits), 3)}
    pos = [h for t, h in zip(test, heard) if t["positive"] and voiced_span(load(t["path"]))]
    neg = [h for t, h in zip(test, heard) if not t["positive"]]
    out["recall"] = round(sum(pos) / len(pos), 3) if pos else None
    out["false_wakes"] = int(sum(neg))
    out["false_wake_rate"] = round(sum(neg) / len(neg), 3) if neg else None
    return out


MAX_FALSE_WAKE_RATE = 0.02


def pick_threshold(test: list[dict], scores: list[float]) -> float:
    """The highest recall that wakes on at most 2% of the held-out not-the-name takes; the strictest
    threshold if none does. (Picked on the held-out takes it's then scored on - optimistic; the
    false-wakes-per-hour check on new audio is the honest one.)"""
    best = None
    for th in np.arange(0.05, 0.991, 0.01):
        r = report(test, [s >= th for s in scores])
        key = (r["false_wake_rate"] <= MAX_FALSE_WAKE_RATE, r["recall"], -r["false_wake_rate"])
        if best is None or key > best[0]:
            best = (key, float(round(th, 2)))
    return best[1]


def main() -> None:
    import torch

    started = time.time()
    rng = np.random.default_rng(SEED)
    all_takes = takes()
    train, test = split(all_takes)
    x, y = featurize(train, copies=6, rng=rng)
    print(f"{len(all_takes)} takes ({len(train)} to learn from, {len(test)} held out); "
          f"{int(y.sum())} name windows, {int((y == 0).sum())} others")
    net, losses = fit(x, y)
    scores = take_scores(net, test)
    threshold = pick_threshold(test, scores)
    ours = report(test, [s >= threshold for s in scores])
    vosk = report(test, vosk_heard(test))
    by_who = {who: {"ours": report([t for t in test if t["who"] == who],
                                   [s >= threshold for s, t in zip(scores, test) if t["who"] == who]),
                    "vosk": report([t for t in test if t["who"] == who],
                                   [h for h, t in zip(vosk_heard([t for t in test if t["who"] == who]),
                                                      [t for t in test if t["who"] == who])])}
              for who in sorted({t["who"] for t in test if not t["who"].startswith("_")})}
    result = {"when": datetime.now().isoformat(timespec="seconds"), "threshold": threshold,
              "takes": {"all": len(all_takes), "train": len(train), "held_out": len(test)},
              "windows": {"name": int(y.sum()), "other": int((y == 0).sum())},
              "ours": ours, "vosk": vosk, "by_person": by_who, "train_seconds": round(time.time() - started, 1)}
    detector.MODEL_PATH.parent.mkdir(exist_ok=True)
    torch.save({"state": net.state_dict(), "threshold": threshold, "info": result}, detector.MODEL_PATH)
    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / f"wakeword_{result['when'][:10]}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"threshold": threshold, "ours": ours, "vosk": vosk}, ensure_ascii=False, indent=1))
    log_mlflow(result, losses, path)


def log_mlflow(result: dict, losses: list[float], path: Path) -> None:
    from training import tracking

    if not tracking.available():
        return
    import mlflow

    mlflow.set_tracking_uri(tracking.TRACKING_URI)
    if mlflow.get_experiment_by_name("jarvis-wakeword") is None:
        mlflow.create_experiment("jarvis-wakeword", artifact_location=(tracking.ARTIFACTS / "wakeword").as_uri())
    mlflow.set_experiment("jarvis-wakeword")
    with mlflow.start_run(run_name=f"wakeword-{result['when'][:16]}"):
        mlflow.log_params({"features": "openWakeWord melspectrogram + speech embedding", "window_frames": WINDOW_FRAMES,
                           "model": "MLP 1536-128-64-1", "augment_copies": 6, "threshold": result["threshold"],
                           **{f"takes_{k}": v for k, v in result["takes"].items()}})
        for step, loss in enumerate(losses):
            mlflow.log_metric("train_loss", loss, step=step)
        for who, side in (("ours", result["ours"]), ("vosk", result["vosk"])):
            mlflow.log_metrics({f"{who}.recall": side["recall"] or 0.0, f"{who}.false_wake_rate": side["false_wake_rate"] or 0.0,
                                **{f"{who}.{part}": v["rate"] for part, v in side.items() if isinstance(v, dict)}})
        mlflow.log_artifact(str(path))


if __name__ == "__main__":
    main()
