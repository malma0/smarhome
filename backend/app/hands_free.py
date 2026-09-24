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
        """wake_class comes from WakeWordDetector.check - only needed while
        asleep; pass None when is_awake() (no name required then)."""
        if self.is_awake():
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

    def force_wake(self) -> None:
        """Same as hearing the bare name - e.g. clicking the orb in the
        desktop window. Also the only way to wake up if Vosk is missing."""
        self._awake_until = self._clock() + self._wake_listen
