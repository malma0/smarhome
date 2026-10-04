"""When Jarvis is awake vs asleep ("do not disturb") in hands-free mode.

Asleep: every phrase goes through the local wake-word check; without the
name it's ignored. "Джарвис, включи свет" (name + command) is handled right
away. "Джарвис" alone plays a short cue and opens a window of
wake_listen_seconds for the command, no name needed.

After every reply, a follow-up window of follow_up_seconds stays open, so a
conversation can go on without repeating the name each time - then Jarvis
falls asleep again. The trade-off: anything said in the room during that
window, even to someone else, is treated as addressed to Jarvis.

Pure state, clock injected - voice_app.py does the audio and the I/O.
"""

import time
from collections.abc import Callable

from app.wake_word import WAKE_ONLY, WAKE_WITH_COMMAND

IGNORE = "ignore"
CUE = "cue"
PROCESS = "process"


# "Стоп" and friends: said alone (or with the name), they stop whatever is
# sounding - a timer, a reminder, an alarm's siren - and end listening.
# Handled locally, never sent to the model.
STOP_WORDS = {
    "стоп", "стой", "хватит", "тихо", "замолчи", "молчи", "замолкни", "отмена", "отменить",
    "перестань", "довольно", "выключись", "отключись", "все", "всё",
}
_NOT_WORDS = {"джарвис", "джервис", "пожалуйста"}


def is_stop_phrase(text: str | None) -> bool:
    words = "".join(c if c.isalnum() or c.isspace() else " " for c in (text or "").casefold()).split()
    words = [w for w in words if w not in _NOT_WORDS]
    return 1 <= len(words) <= 3 and all(w in STOP_WORDS for w in words)


def name_heard(text: str | None, wake_words) -> bool:
    """The name anywhere in what's heard so far - over Jarvis's own reply it
    interrupts, the way "Alexa" does over Alexa. Checked on the words as they
    come: a reply never pauses long enough for "Джарвис, стоп" to be a phrase
    of its own (measured: the reply and the command came out as one 12-second
    phrase), and "стоп" itself drowned in the reply while the name didn't."""
    words = "".join(c if c.isalnum() or c.isspace() else " " for c in (text or "").casefold()).split()
    return any(w in words for w in wake_words)


_NAME_FILLERS = {"эй", "слушай", "привет", "ну", "а", "о", "ой", "окей", "ок", "так", "да", "алло"}


def _edits(a: str, b: str) -> int:
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, cb in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ca != cb))
    return row[-1]


def sounds_like_name(word: str, wake_words) -> bool:
    """The name, or Whisper's near miss of it: "Дайвис", "Джарвиз", "Жарвис"
    - two letters off at most, about as long."""
    return any(_edits(word, w) <= 2 and abs(len(word) - len(w)) <= 2 for w in wake_words)


def only_the_name(text: str | None, wake_words) -> bool:
    """Whisper heard nothing but the name (maybe misheard) - a call, not a
    request: "Джарвис" said twice came back as "Дайвис" and got an answer
    about the town of Davis, California."""
    words = "".join(c if c.isalnum() or c.isspace() else " " for c in (text or "").casefold()).split()
    words = [w for w in words if w not in _NAME_FILLERS]
    return bool(words) and len(words) <= 2 and all(sounds_like_name(w, wake_words) for w in words)


class HandsFreeState:
    def __init__(
        self,
        wake_listen_seconds: float = 8.0,
        follow_up_seconds: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._wake_listen = wake_listen_seconds
        self._follow_up = follow_up_seconds
        self._clock = clock
        self._awake_until: float | None = None

    def is_awake(self) -> bool:
        return self._awake_until is not None and self._clock() < self._awake_until

    def on_phrase(self, wake_class: str | None) -> str:
        """wake_class comes from WakeWordDetector.check. Awake, no name is
        needed - but the name alone still means "listen": in the follow-up
        window "Джарвис" (pause) "сделай потемнее" went to the model as two
        messages, and the bare name got "Здравствуй!"."""
        if self.is_awake():
            if wake_class == WAKE_ONLY:
                self._awake_until = self._clock() + self._wake_listen
                return CUE
            # Handling it now; the window reopens after the reply, counted
            # from then - not from before a possibly minute-long answer.
            self._awake_until = None
            return PROCESS
        if wake_class == WAKE_WITH_COMMAND:
            return PROCESS
        if wake_class == WAKE_ONLY:
            self._awake_until = self._clock() + self._wake_listen
            return CUE
        return IGNORE

    def after_reply(self) -> None:
        self._awake_until = self._clock() + self._follow_up

    def sleep(self) -> None:
        """Back to waiting for the name - e.g. the mic was switched off."""
        self._awake_until = None

    def force_wake(self) -> None:
        """Same as hearing the bare name - e.g. clicking the orb in the
        desktop window. Also the only way to wake up if Vosk is missing."""
        self._awake_until = self._clock() + self._wake_listen
