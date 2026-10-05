"""A guided recording session for the own wake-word model: the screen says
what to say, a beep, 2.5 s recorded, the next one - nothing to press.

    python -m wakeword.record --who Матвей              (the whole session, ~12 minutes)
    python -m wakeword.record --who Эля --part far      (only one part)

Parts: near (the name, from where you usually sit), far (the name from 2-3 m),
similar (words that sound like it - what Vosk took the name for, and what it
mustn't wake on), speech (ordinary commands and talk, no name), quiet (the
room with nobody talking). Each take is a 16 kHz mono WAV in
wakeword/data/<who>/<part>/ - the residents' own voices: gitignored, never
leaves the machine. Close Jarvis first, or it answers its name meanwhile.
"""

import argparse
import random
import sys
import time
import wave
from pathlib import Path

import numpy as np

DATA = Path(__file__).resolve().parent / "data"
RATE = 16000
TAKE_SECONDS = 2.5
PRE_BEEP_SECONDS = 0.5  # recording starts before the beep: Матвей's first takes began on the beep, the name cut short

NAME_VARIANTS = ["Джарвис", "Джарвис", "Джарвис", "Джарвис!", "Джарвис?", "Эй, Джарвис", "Джарвис, слушай"]
# what the small Vosk model heard instead of the name (wake_debug.log), and what the docs say it
# fired on with a two-word grammar - the words a wake-word model gets wrong first
SIMILAR = ["Джордж", "Джой", "Джекс", "даже с", "кажется", "жарко", "Дарвин", "джинсы", "Чарли", "Джерри",
           "Джеймс", "Гарри", "Харви", "Марвин", "Жанна", "журнал", "джаз", "Джексон", "жарить", "Джон",
           "Джавид", "Ярослав", "Джулия", "служба", "тяжесть", "Джим", "шарф", "жара", "Джамал", "Дарья"]
SPEECH = ["Включи свет на кухне", "Какая температура в спальне", "Закрой шторы в зале", "Сделай потеплее",
          "Сколько сейчас времени", "Выключи везде свет", "Поставь чайник", "Где мои ключи",
          "Пойдём гулять вечером", "Спокойной ночи", "Доброе утро", "Мы уходим", "Ты где", "Привет, как дела",
          "Что на ужин", "Открой окно", "Мне холодно", "Позвони маме", "Подожди минутку", "Да, конечно"]

PARTS = {
    "near": ("Имя — с обычного места, обычным голосом", NAME_VARIANTS, 50),
    "far": ("Имя — отойди на 2–3 метра от микрофона", NAME_VARIANTS, 20),
    "similar": ("Похожие слова — говори как обычно, это НЕ имя", SIMILAR, 30),
    "speech": ("Обычные фразы без имени", SPEECH, 20),
    "quiet": ("Тишина — ничего не говори, просто сиди", ["(молчи)"], 10),
}
ORDER = ["near", "similar", "speech", "far", "quiet"]


def beep() -> None:
    try:
        import winsound

        winsound.Beep(1000, 120)
    except Exception:  # noqa: BLE001 - no speaker: the screen still says when
        pass


def record_take(sd, device) -> np.ndarray:
    """Recording already runs when the beep sounds - a word started on the beep is kept whole."""
    audio = sd.rec(int((PRE_BEEP_SECONDS + TAKE_SECONDS) * RATE), samplerate=RATE, channels=1, dtype="int16",
                   device=device)
    time.sleep(PRE_BEEP_SECONDS)
    beep()
    sd.wait()
    return audio[:, 0]


def save(audio: np.ndarray, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{time.strftime('%Y%m%dT%H%M%S')}_{len(list(folder.glob('*.wav'))):03d}.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(audio.tobytes())
    return path


def level(audio: np.ndarray) -> float:
    return 20 * np.log10(np.abs(audio.astype(np.float32)).max() / 32768 + 1e-9)


def run_part(sd, device, who: str, part: str) -> int:
    title, words, count = PARTS[part]
    prompts = [words[i % len(words)] for i in range(count)]
    random.shuffle(prompts)
    print(f"\n=== {title} — {count} раз ===")
    if part == "far":
        input("Отойди на 2–3 метра от микрофона и нажми Enter...")
    else:
        input("Нажми Enter, когда готов...")
    quiet_takes = 0
    for n, word in enumerate(prompts, 1):
        print(f"\n[{n}/{count}]   >>>  {word}  <<<", flush=True)
        time.sleep(0.3)
        audio = record_take(sd, device)
        save(audio, DATA / who / part)
        loud = level(audio)
        if part != "quiet" and loud < -40:
            quiet_takes += 1
            print(f"   (очень тихо: {loud:.0f} дБ — говори громче или ближе)")
        time.sleep(0.4)
    return quiet_takes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--who", required=True, help="whose voice: Матвей, Эля...")
    parser.add_argument("--part", choices=list(PARTS), help="only this part (default: all, in order)")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    import sounddevice as sd

    from app.audio_capture import input_device

    device = input_device()
    name = sd.query_devices(device if device is not None else sd.default.device[0])["name"]
    print(f"Запись голоса для своего детектора имени. Кто: {args.who}. Микрофон: {name}")
    print("После сигнала — 2,5 секунды, говори сразу после писка. Окно Джарвиса должно быть закрыто.")
    for part in [args.part] if args.part else ORDER:
        run_part(sd, device, args.who, part)
    print("\nГотово, спасибо! Можно закрыть это окно.")


if __name__ == "__main__":
    main()
