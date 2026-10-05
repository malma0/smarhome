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


ACAV = HERE / "negatives" / "openwakeword_features_ACAV100M_2000_hrs_16bit.npy"
VALIDATION = HERE / "negatives" / "validation_set_features.npy"
ACAV_PER_EPOCH = 200_000  # fresh other-audio windows each epoch, out of 5.6 million
TARGET_FALSE_PER_HOUR = 0.5
REFRACTORY_FRAMES = 25  # 2 s: firing on consecutive windows is one wake


def acav_sample(acav: np.ndarray, n: int, rng: np.random.Generator, block: int = 2000) -> np.ndarray:
    """n windows of the 2000 hours, in random contiguous blocks (one disk read each)."""
    starts = rng.integers(0, acav.shape[0] - block, n // block)
    return np.concatenate([np.asarray(acav[s:s + block], dtype=np.float32) for s in starts])


def fit(x: np.ndarray, y: np.ndarray, acav: np.ndarray | None, epochs: int = 12):
    """The residents' windows every epoch, plus a fresh sample of other audio as negatives."""
    import torch

    torch.manual_seed(SEED)
    rng = np.random.default_rng(SEED)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    net = detector.build_model().to(device)
    opt = torch.optim.Adam(net.parameters(), lr=1e-3, weight_decay=1e-4)
    losses = []
    for _ in range(epochs):
        if acav is not None:
            other = acav_sample(acav, ACAV_PER_EPOCH, rng)
            X = np.concatenate([x, other])
            Y = np.concatenate([y, np.zeros(len(other), dtype=np.float32)])
        else:
            X, Y = x, y
        pos_weight = torch.tensor([min(200.0, (Y == 0).sum() / max(1, (Y == 1).sum()))], device=device)
        loss_fn = torch.nn.BCEWithLogitsLoss(pos_weight=pos_weight)
        Xt, Yt = torch.from_numpy(X).to(device), torch.from_numpy(Y).to(device)
        net.train()
        perm = torch.randperm(len(Xt), device=device)
        total = 0.0
        for i in range(0, len(Xt), 1024):
            idx = perm[i:i + 1024]
            opt.zero_grad()
            loss = loss_fn(net(Xt[idx])[:, 0], Yt[idx])
            loss.backward()
            opt.step()
            total += float(loss) * len(idx)
        losses.append(total / len(Xt))
        print(f"  epoch {len(losses)}: loss {losses[-1]:.4f}", flush=True)
    net.eval()
    return net.cpu(), losses


def take_scores(net, test: list[dict]) -> list[float]:
    import torch

    out = []
    with torch.no_grad():
        for take in test:
            w = torch.from_numpy(detector.windows(detector.embed(load(take["path"]))).astype(np.float32))
            out.append(float(torch.sigmoid(net(w)).max()))
    return out


def stream_scores(net, features: np.ndarray) -> np.ndarray:
    """The model over a long stream of embedding frames, one window per 80 ms - as Jarvis would run it."""
    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    net = net.to(device)
    out = []
    with torch.no_grad():
        for start in range(0, features.shape[0] - WINDOW_FRAMES, 50_000):
            chunk = np.asarray(features[start:start + 50_000 + WINDOW_FRAMES - 1], dtype=np.float32)
            w = np.ascontiguousarray(np.lib.stride_tricks.sliding_window_view(chunk, (WINDOW_FRAMES, 96))[:, 0])
            out.append(torch.sigmoid(net(torch.from_numpy(w).to(device))).cpu().numpy()[:, 0])
    net.cpu()
    return np.concatenate(out)


def wakes(scores: np.ndarray, threshold: float) -> int:
    above = np.where(scores >= threshold)[0]
    if not len(above):
        return 0
    return int(1 + (np.diff(above) > REFRACTORY_FRAMES).sum())


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


def curve(test: list[dict], scores: list[float], stream: np.ndarray, hours: float) -> list[dict]:
    """Threshold -> recall on the held-out names, false wakes per hour of other audio."""
    rows = []
    for th in [0.5, 0.7, 0.8, 0.9, 0.95, 0.97, 0.98, 0.99, 0.995, 0.998, 0.999]:
        r = report(test, [s >= th for s in scores])
        rows.append({"threshold": th, "recall": r["recall"], "false_per_hour": round(wakes(stream, th) / hours, 2),
                     "false_wake_rate_takes": r["false_wake_rate"]})
    return rows


def pick_threshold(rows: list[dict]) -> float:
    """The best recall at no more than TARGET_FALSE_PER_HOUR; the quietest threshold if none gets there.
    (Chosen on the 11-hour validation set it's then scored on, as openWakeWord does - the live shadow
    run is the independent check.)"""
    ok = [r for r in rows if r["false_per_hour"] <= TARGET_FALSE_PER_HOUR]
    if ok:
        return max(ok, key=lambda r: (r["recall"], -r["threshold"]))["threshold"]
    return min(rows, key=lambda r: (r["false_per_hour"], -r["recall"]))["threshold"]


def main() -> None:
    import torch

    started = time.time()
    rng = np.random.default_rng(SEED)
    all_takes = takes()
    train, test = split(all_takes)
    x, y = featurize(train, copies=6, rng=rng)
    acav = np.load(ACAV, mmap_mode="r") if ACAV.exists() else None
    other = f"{acav.shape[0]} windows" if acav is not None else "none"
    print(f"{len(all_takes)} takes ({len(train)} to learn from, {len(test)} held out); {int(y.sum())} name windows, "
          f"{int((y == 0).sum())} others; other audio: {other}")
    net, losses = fit(x, y, acav)
    scores = take_scores(net, test)
    validation = np.load(VALIDATION, mmap_mode="r")
    hours = validation.shape[0] * detector.FRAME_SECONDS / 3600
    stream = stream_scores(net, validation)
    rows = curve(test, scores, stream, hours)
    threshold = pick_threshold(rows)
    ours = report(test, [s >= threshold for s in scores])
    ours["false_per_hour"] = round(wakes(stream, threshold) / hours, 2)
    vosk_flags = vosk_heard(test)
    vosk = report(test, vosk_flags)
    both = report(test, [(s >= threshold) or v for s, v in zip(scores, vosk_flags)])
    by_who = {}
    for who in sorted({t["who"] for t in test if not t["who"].startswith("_")}):
        mine = [i for i, t in enumerate(test) if t["who"] == who]
        sub = [test[i] for i in mine]
        by_who[who] = {"ours": report(sub, [scores[i] >= threshold for i in mine]),
                       "vosk": report(sub, [vosk_flags[i] for i in mine])}
    result = {"when": datetime.now().isoformat(timespec="seconds"), "threshold": threshold,
              "takes": {"all": len(all_takes), "train": len(train), "held_out": len(test)},
              "windows": {"name": int(y.sum()), "other": int((y == 0).sum()),
                          "other_audio_per_epoch": ACAV_PER_EPOCH if acav is not None else 0},
              "validation_hours": round(hours, 1), "curve": rows,
              "ours": ours, "vosk": vosk, "vosk_or_ours": both, "by_person": by_who,
              "train_seconds": round(time.time() - started, 1)}
    detector.MODEL_PATH.parent.mkdir(exist_ok=True)
    torch.save({"state": net.state_dict(), "threshold": threshold, "info": result}, detector.MODEL_PATH)
    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / f"wakeword_{result['when'][:10]}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"threshold {threshold}")
    for r in rows:
        print(f"  {r['threshold']:6}: recall {r['recall']}, {r['false_per_hour']} false/hour")
    for name, side in (("ours", ours), ("vosk", vosk), ("vosk_or_ours", both)):
        extra = f", {side['false_per_hour']} false/hour" if "false_per_hour" in side else ""
        print(f"{name:13} recall {side['recall']}, false on held-out takes {side['false_wakes']} "
              f"({side['false_wake_rate']}){extra}")
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
                           "model": "LayerNorm + MLP 1536-128-64-1", "augment_copies": 6,
                           "other_audio_per_epoch": result["windows"]["other_audio_per_epoch"],
                           "threshold": result["threshold"], "validation_hours": result["validation_hours"],
                           **{f"takes_{k}": v for k, v in result["takes"].items()}})
        for step, loss in enumerate(losses):
            mlflow.log_metric("train_loss", loss, step=step)
        for name, side in (("ours", result["ours"]), ("vosk", result["vosk"]), ("vosk_or_ours", result["vosk_or_ours"])):
            mlflow.log_metrics({f"{name}.recall": side["recall"] or 0.0,
                                f"{name}.false_wake_rate": side["false_wake_rate"] or 0.0,
                                **{f"{name}.{part}": v["rate"] for part, v in side.items() if isinstance(v, dict)}})
        mlflow.log_metric("ours.false_per_hour", result["ours"]["false_per_hour"])
        mlflow.log_artifact(str(path))


if __name__ == "__main__":
    main()
