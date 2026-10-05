"""Ordinary speech without the name, made offline by Windows' Russian voice
(SAPI, Irina) - negatives for the wake-word model, which learned the
residents' 100 not-the-name takes by heart and woke on new speech half the
time. The phrases are the home model's training phrasings without "Джарвис";
each said at a few speeds and pitches (resampled), cut to 2.5 s takes.

    python -m wakeword.synth --count 400      -> wakeword/data/_tts/speech/
"""

import argparse
import json
import random
import tempfile
import wave
from pathlib import Path

import numpy as np

from wakeword.record import DATA, RATE, TAKE_SECONDS

PHRASES = Path(__file__).resolve().parents[1] / "training" / "data"


def phrases(count: int, seed: int = 0) -> list[str]:
    said = []
    for path in sorted(PHRASES.glob("train*.jsonl")):
        for line in path.open(encoding="utf-8"):
            text = next(m["content"] for m in json.loads(line)["messages"] if m["role"] == "user").split("\n\n[")[0]
            low = text.casefold()
            if "джарв" not in low and "джерв" not in low and 3 <= len(text.split()) <= 9:
                said.append(text)
    random.Random(seed).shuffle(said)
    return list(dict.fromkeys(said))[:count]


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path)) as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        if w.getnchannels() > 1:
            audio = audio.reshape(-1, w.getnchannels())[:, 0]
        return audio.astype(np.float32), w.getframerate()


def to_takes(audio: np.ndarray, rate: int, factor: float) -> list[np.ndarray]:
    """Resampled to RATE with a speed/pitch factor, cut into 2.5 s takes (the speech parts)."""
    from scipy.signal import resample

    n = int(len(audio) * RATE / rate / factor)
    x = resample(audio, n).clip(-32768, 32767).astype(np.int16)
    size = int(TAKE_SECONDS * RATE)
    return [x[i:i + size] for i in range(0, max(1, len(x) - size // 2), size) if np.abs(x[i:i + size]).max() > 500]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=400, help="phrases to say")
    args = parser.parse_args()
    import pyttsx3

    engine = pyttsx3.init()
    voice = next(v for v in engine.getProperty("voices") if "RU" in v.id.upper())
    engine.setProperty("voice", voice.id)
    rng = random.Random(1)
    out = DATA / "_tts" / "speech"
    out.mkdir(parents=True, exist_ok=True)
    made = 0
    with tempfile.TemporaryDirectory() as tmp:
        texts = phrases(args.count)
        paths = []
        for i, text in enumerate(texts):
            engine.setProperty("rate", rng.choice([150, 170, 190, 210]))
            paths.append(Path(tmp) / f"{i}.wav")
            engine.save_to_file(text, str(paths[-1]))
        engine.runAndWait()
        for i, path in enumerate(paths):
            if not path.exists():
                continue
            audio, rate = read_wav(path)
            for take in to_takes(audio, rate, rng.choice([0.85, 0.92, 1.0, 1.08, 1.15])):
                take = np.pad(take, (0, int(TAKE_SECONDS * RATE) - len(take)))
                with wave.open(str(out / f"tts_{i:04d}_{made:05d}.wav"), "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(RATE)
                    w.writeframes(take.tobytes())
                made += 1
    print(f"{made} takes of ordinary speech from {len(texts)} phrases -> {out}")


if __name__ == "__main__":
    main()
