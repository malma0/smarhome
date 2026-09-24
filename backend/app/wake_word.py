"""Local wake-word detection for hands-free mode (voice_app.py).

While Jarvis is "asleep", every phrase spoken in the room is checked here -
entirely on this machine, with Vosk's small Russian model - for its name.
Phrases without it are dropped on the spot: never sent to Whisper/Groq,
never saved. Only once the name is heard does anything leave the machine.

Why full-vocabulary recognition and not a one-word grammar: restricting
Vosk to ["джарвис", "[unk]"] caught every test phrase with the name but
also fired, at confidence 1.00, on "Джордж", "жарко", "Чарли" and
"джинсы" - with only two choices, anything vaguely similar gets pulled to
the name. Letting it pick from its whole vocabulary had zero false
triggers on the same test set, since "Джордж" is then just "джордж".

The male test voice came out as "джервис" rather than "джарвис" - both
are words in the model's vocabulary (probed directly), so both count. The
only other near neighbor there is "дарвин", which is deliberately not a
wake word.

Vosk only decides "was the name said, and was anything said after it". The
command itself is still transcribed by Whisper - Vosk's small model is far
less accurate for that.
"""

import json

DEFAULT_WAKE_WORDS = ("джарвис", "джервис")

# Said around the name without being a command: "Эй, Джарвис!" alone
# should get the "listening" cue, not be sent off as a request.
FILLER_WORDS = frozenset({"эй", "слушай", "привет", "ну", "а", "о", "ой", "окей", "ок", "так", "да"})

NONE = "none"
WAKE_ONLY = "wake_only"
WAKE_WITH_COMMAND = "wake_with_command"


def parse_wake_words(raw: str) -> tuple[str, ...]:
    words = tuple(w.strip().lower() for w in raw.split(",") if w.strip())
    return words or DEFAULT_WAKE_WORDS


def classify(text: str, wake_words: tuple[str, ...]) -> str:
    words = text.lower().split()
    if not any(word in wake_words for word in words):
        return NONE
    rest = [word for word in words if word not in wake_words and word not in FILLER_WORDS]
    return WAKE_WITH_COMMAND if rest else WAKE_ONLY


class WakeWordDetector:
    def __init__(self, model_path: str, wake_words: tuple[str, ...] = DEFAULT_WAKE_WORDS, sample_rate: int = 16000):
        from vosk import Model, SetLogLevel

        SetLogLevel(-1)  # Kaldi otherwise logs every model component to the console
        self._model = Model(model_path)
        self._wake_words = wake_words
        self._sample_rate = sample_rate

    def transcribe(self, pcm16: bytes) -> str:
        from vosk import KaldiRecognizer

        recognizer = KaldiRecognizer(self._model, self._sample_rate)
        recognizer.AcceptWaveform(pcm16)
        return json.loads(recognizer.FinalResult()).get("text", "")

    def check(self, pcm16: bytes) -> str:
        return classify(self.transcribe(pcm16), self._wake_words)
