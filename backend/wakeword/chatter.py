"""A false-wake test that needs nobody to talk: minutes of ordinary Russian
speech in many synthetic voices (no "Джарвис" in it), played through a
speaker in the room while Jarvis listens with its microphone. Every wake in
wake_debug.log meanwhile is a false one.

    python -m wakeword.chatter --minutes 15 --make        (writes wakeword/negatives/chatter.wav)
    python -m wakeword.chatter --play --device "JBL"      (plays it through that speaker)
"""

import argparse
import random
import wave
from pathlib import Path

import numpy as np

from wakeword.record import RATE, SIMILAR, SPEECH
from wakeword.synth import phrases
from wakeword.synth_voices import MORE_SIMILAR, SPEAKERS

OUT = Path(__file__).resolve().parent / "negatives" / "chatter.wav"


def make(minutes: float) -> None:
    import torch
    from scipy.signal import resample_poly

    model, _ = torch.hub.load("snakers4/silero-models", "silero_tts", language="ru", speaker="v4_ru",
                              trust_repo=True, verbose=False)
    model.to("cuda" if torch.cuda.is_available() else "cpu")
    pick = random.Random(11)
    # mostly talk, now and then a word like the name - the hard cases, as in a real room
    talk = phrases(2000, seed=5) + SPEECH
    near_misses = SIMILAR + MORE_SIMILAR
    pieces, total = [], 0
    while total < minutes * 60 * RATE:
        text = pick.choice(near_misses) if pick.random() < 0.1 else pick.choice(talk)
        try:
            voice = model.apply_tts(text=text, speaker=pick.choice(SPEAKERS), sample_rate=48000)
        except Exception:  # noqa: BLE001 - a text it can't say
            continue
        audio = resample_poly(voice.cpu().numpy().astype(np.float64), 1, 3)
        gap = np.zeros(int(pick.uniform(0.3, 2.5) * RATE))  # people pause - and phrases end, as Vosk sees them
        pieces += [audio * 0.7 * 32767, gap]
        total += len(audio) + len(gap)
    x = np.clip(np.concatenate(pieces), -32768, 32767).astype(np.int16)
    OUT.parent.mkdir(exist_ok=True)
    with wave.open(str(OUT), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(x.tobytes())
    print(f"{len(x) / RATE / 60:.1f} min of speech -> {OUT}")


def play(device: str) -> None:
    import sounddevice as sd

    index = next(i for i, d in enumerate(sd.query_devices())
                 if d["max_output_channels"] > 0 and device.casefold() in d["name"].casefold())
    with wave.open(str(OUT)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    print(f"playing {len(x) / RATE / 60:.1f} min through {sd.query_devices(index)['name']}", flush=True)
    sd.play(x, RATE, device=index)
    sd.wait()
    print("done")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=float, default=15)
    parser.add_argument("--make", action="store_true")
    parser.add_argument("--play", action="store_true")
    parser.add_argument("--device", default="JBL")
    args = parser.parse_args()
    if args.make:
        make(args.minutes)
    if args.play:
        play(args.device)


if __name__ == "__main__":
    main()
