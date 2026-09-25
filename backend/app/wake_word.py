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
    """One recognizer, reset between phrases and warmed up at startup.
    Measured: a fresh recognizer per phrase cost ~250 ms to create plus
    ~750 ms for its first half-second of audio - about a second of stall at
    the start of every phrase, in the same thread as the voice-activity
    detection. Reused and warmed up, the same phrase start costs 15-30 ms
    and a whole 3.6 s phrase ~230 ms.

    Not thread-safe: the shared recognizer serves one phrase at a time
    (HandsFreeListener only ever feeds one, from its own thread)."""

    def __init__(self, model_path: str, wake_words: tuple[str, ...] = DEFAULT_WAKE_WORDS, sample_rate: int = 16000):
        from vosk import KaldiRecognizer, Model, SetLogLevel

        SetLogLevel(-1)  # Kaldi otherwise logs every model component to the console
        self._model = Model(model_path)
        self.wake_words = wake_words
        self._recognizer = KaldiRecognizer(self._model, sample_rate)
        self._warm_up(sample_rate)

    def _warm_up(self, sample_rate: int) -> None:
        import numpy as np

        noise = np.random.default_rng(0).normal(0, 300, sample_rate).astype(np.int16)
        self.transcribe(noise.tobytes())

    def transcribe(self, pcm16: bytes) -> str:
        stream = self.stream()
        stream.feed(pcm16)
        return stream.finish()

    def check(self, pcm16: bytes) -> str:
        return classify(self.transcribe(pcm16), self.wake_words)

    def stream(self) -> "StreamingTranscript":
        self._recognizer.Reset()
        return StreamingTranscript(self._recognizer)


class StreamingTranscript:
    """Recognition fed while the phrase is still being spoken, so the name
    check is ready the moment it ends. Checking the finished phrase instead
    measured ~1.1s for 3.6s of speech - all of it added after the person
    had already stopped talking.

    Vosk finalizes segments on its own pauses: a True from AcceptWaveform
    means that segment's text is in Result() and won't reappear in
    FinalResult(), so every segment has to be collected along the way."""

    def __init__(self, recognizer):
        self._recognizer = recognizer
        self._segments: list[str] = []

    def feed(self, pcm16: bytes) -> None:
        if self._recognizer.AcceptWaveform(pcm16):
            self._collect(self._recognizer.Result())

    def finish(self) -> str:
        self._collect(self._recognizer.FinalResult())
        return " ".join(self._segments)

    def so_far(self) -> str:
        """What's been heard of the phrase so far, while it's still going."""
        partial = json.loads(self._recognizer.PartialResult()).get("partial", "").strip()
        return " ".join([*self._segments, partial]).strip()

    def _collect(self, result_json: str) -> None:
        text = json.loads(result_json).get("text", "").strip()
        if text:
            self._segments.append(text)
