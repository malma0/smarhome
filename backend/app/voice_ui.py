"""What the voice loop reports, separated from how it's shown.

voice_app.py used to print() straight to the terminal. Now it reports
events through a VoiceUI - "listening", "thinking", "you said...", "Jarvis
replied...", mic level, the loudness envelope of the reply being spoken -
and each front end decides how to show them: ConsoleUI prints them like
before, the desktop window (jarvis_gui.py) turns them into an animated orb
and chat bubbles.

States: "sleeping" (waiting for the name), "listening", "thinking"
(transcribing / answering), "speaking".
"""

from collections.abc import Callable
from typing import Protocol

SLEEPING = "sleeping"
LISTENING = "listening"
THINKING = "thinking"
SPEAKING = "speaking"


class VoiceUI(Protocol):
    def state(self, state: str, detail: str = "") -> None: ...

    def user_said(self, text: str, voice: bool) -> None: ...

    def utterance_saved(self, utterance_id: str) -> None:
        """The last user message was stored in the training dataset under
        this id - lets a front end offer "correct this" on that message."""
        ...

    def jarvis_said(self, text: str, actions: list[dict]) -> None: ...

    def info(self, text: str) -> None: ...

    def resident(self, name: str) -> None: ...

    def mic_level(self, level: float) -> None:
        """0..1, while listening - a few times per second."""
        ...

    def speech_envelope(self, levels: list[float], frame_seconds: float) -> None:
        """Loudness of the reply about to play, one 0..1 value per frame -
        sent once at playback start so a front end can animate in sync."""
        ...

    def ask_name(self, prompt: str) -> str:
        """Blocking; "" means the person declined."""
        ...


class ConsoleUI:
    def __init__(self, ask: Callable[[str], str] = input):
        self.ask = ask

    def state(self, state: str, detail: str = "") -> None:
        if detail:
            print(detail)

    def user_said(self, text: str, voice: bool) -> None:
        print(f"you> {text}")

    def utterance_saved(self, utterance_id: str) -> None:
        pass

    def jarvis_said(self, text: str, actions: list[dict]) -> None:
        print(f"jarvis> {text}")
        for action in actions:
            print(f"   [action] {action['tool']}({action['input']}) -> {action['result']}")
        print()

    def info(self, text: str) -> None:
        print(text)

    def resident(self, name: str) -> None:
        print(f"(голос: {name})")

    def mic_level(self, level: float) -> None:
        pass

    def speech_envelope(self, levels: list[float], frame_seconds: float) -> None:
        pass

    def ask_name(self, prompt: str) -> str:
        try:
            return self.ask(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            return ""
