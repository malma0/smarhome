"""Thousands of voices saying "Джарвис" - and the same voices saying what isn't
the name - for the wake-word model. Two residents' 150 takes taught it what
those two voices sound like, not the word: 17 false wakes an hour on other
people's audio. This is openWakeWord's recipe, with Silero's offline Russian
TTS (a new random voice per take) instead of English ones.

    python -m wakeword.synth_voices --names 3000 --others 3000

Each take: a speed/pitch change, half of them in a simulated room (reverb),
most over a faint hiss (a real room is never digital zero), at a random
level, the word ending somewhere in a 2.5 s take. Written to
wakeword/data/_synth/{near,similar,speech}/ - made, not anyone's voice.
Silero models: CC BY-NC-SA 4.0 (non-commercial - fine for this project).
"""

import argparse
import random
import wave
from pathlib import Path

import numpy as np

from wakeword.record import DATA, RATE, SIMILAR, SPEECH, TAKE_SECONDS
from wakeword.synth import phrases

NAMES = ["Джарвис", "Джарвис!", "Джарвис?", "Джарвис.", "Эй, Джарвис", "Джарвис!!", "Ну, Джарвис"]
# more words like the name, beyond the ones the residents said (wakeword/record.py)
MORE_SIMILAR = ["Джарвин", "Дарвис", "Харвис", "Мавис", "Чарльз", "Джерси", "жалюзи", "Джанни", "Дарвиш", "Джастин",
                "Жорж", "Джавелин", "Ярвис", "Джабраил", "Тарас", "Джокер", "Сервис", "Морис", "Борис", "Давид",
                "Джавид", "Джордан", "Джексон", "Джамбул", "Чарли", "Ирис", "Гарвард", "Жанна", "Жасмин", "Джинн"]
SPEAKERS = ["random"] * 8 + ["aidar", "baya", "kseniya", "xenia", "eugene"]


def room(rng: np.random.Generator) -> np.ndarray:
    """A synthetic room impulse response: a decaying noise tail, RT60 0.2-0.8 s."""
    rt60 = rng.uniform(0.2, 0.8)
    n = int(rt60 * RATE)
    t = np.arange(n) / RATE
    ir = rng.normal(0, 1, n) * np.exp(-6.9 * t / rt60)
    ir[0] = 1.0
    return ir / np.abs(ir).sum() * 4


def place(voice: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """The voice in a 2.5 s take: changed, in a room, over a hiss, ending at a random moment."""
    from scipy.signal import fftconvolve, resample

    factor = rng.uniform(0.85, 1.18)  # faster or slower, higher or lower
    x = resample(voice, int(len(voice) / factor))
    if rng.random() < 0.5:
        x = 0.6 * x + 0.4 * fftconvolve(x, room(rng))[: len(x) + int(0.3 * RATE)][: len(x)]
    x = x / (np.abs(x).max() + 1e-9) * 10 ** (rng.uniform(-30, -3) / 20) * 32767
    size = int(TAKE_SECONDS * RATE)
    take = np.zeros(size)
    if len(x) >= size:
        take = x[:size]
    else:
        end = rng.integers(len(x), size + 1)  # the word ends anywhere it fits
        take[end - len(x):end] = x
    if rng.random() < 0.8:
        take += rng.normal(0, 10 ** (rng.uniform(-75, -45) / 20) * 32767, size)
    return np.clip(take, -32768, 32767).astype(np.int16)


def write(take: np.ndarray, path: Path) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(take.tobytes())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--names", type=int, default=3000)
    parser.add_argument("--others", type=int, default=3000)
    args = parser.parse_args()
    import torch
    from scipy.signal import resample_poly

    model, _ = torch.hub.load("snakers4/silero-models", "silero_tts", language="ru", speaker="v4_ru",
                              trust_repo=True, verbose=False)
    model.to("cuda" if torch.cuda.is_available() else "cpu")
    rng = np.random.default_rng(7)
    pick = random.Random(7)
    words = SIMILAR + MORE_SIMILAR
    talk = SPEECH + phrases(600, seed=3)
    jobs = ([("near", pick.choice(NAMES)) for _ in range(args.names)]
            + [("similar", pick.choice(words)) for _ in range(args.others // 2)]
            + [("speech", pick.choice(talk)) for _ in range(args.others - args.others // 2)])
    for part in ("near", "similar", "speech"):
        (DATA / "_synth" / part).mkdir(parents=True, exist_ok=True)
    made = 0
    for i, (part, text) in enumerate(jobs):
        try:
            voice = model.apply_tts(text=text, speaker=pick.choice(SPEAKERS), sample_rate=48000)
        except Exception:  # noqa: BLE001 - a text it can't say: skip it
            continue
        audio = resample_poly(voice.cpu().numpy().astype(np.float64), 1, 3)  # 48 -> 16 kHz
        write(place(audio, rng), DATA / "_synth" / part / f"{part}_{i:05d}.wav")
        made += 1
        if made % 500 == 0:
            print(f"  {made}/{len(jobs)}", flush=True)
    print(f"{made} synthetic takes -> {DATA / '_synth'}")


if __name__ == "__main__":
    main()
