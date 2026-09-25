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
MUTED = "muted"  # the microphone is switched off - nothing is heard, not even the name


class VoiceUI(Protocol):
    def state(self, state: str, detail: str = "") -> None: ...

    def user_said(
        self,
        text: str,
        voice: bool,
        utterance_id: str | None = None,
        speaker: str | None = None,
        speaker_sure: bool = True,
    ) -> None:
        """text is "" when a spoken phrase couldn't be made out at all -
        still shown, so it can be corrected. utterance_id: where the phrase
        is stored in the training dataset (None for typed messages or with
        the dataset off) - lets a front end offer "correct this" right away.
        speaker: who Jarvis thinks said it (voice phrases); speaker_sure is
        False for a guess (closer to one person, or "whoever spoke last") -
        a front end can offer "who was it?" to fix the label."""
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

    def ask_name(self, prompt: str, known: list[str] = ()) -> str:
        """Blocking; "" means the person declined. known: names that already
        have a voice profile - picking one adds this recording to it."""
        ...

    def voices(self, profiles: list[dict]) -> None:
        """Everyone with a voice profile: [{"name": ..., "samples": n}]."""
        ...

    def alert(self, text: str, key: str, active: bool) -> None:
        """A danger (app.danger): active=True raises it - shown until the
        same key comes back with active=False."""
        ...

    def ring(self, text: str, key: str, active: bool) -> None:
        """A timer or reminder going off (active=True) until stopped or it
        gives up (active=False)."""
        ...

    def enrollment(self, name: str, collected: int, needed: int, status: str) -> None:
        """Deliberate voice recording for one person. status: "started",
        "progress", "done", "partial" (timed out with some samples),
        "failed" (timed out with none), "cancelled"."""
        ...


class ConsoleUI:
    def __init__(self, ask: Callable[[str], str] = input):
        self.ask = ask

    def state(self, state: str, detail: str = "") -> None:
        if detail:
            print(detail)

    def user_said(
        self,
        text: str,
        voice: bool,
        utterance_id: str | None = None,
        speaker: str | None = None,
        speaker_sure: bool = True,
    ) -> None:
        who = f"{speaker}{'' if speaker_sure else '?'}" if speaker and speaker != "default" else "you"
        print(f"{who}> {text}" if text else "(не удалось разобрать речь - если я ошибся, впиши, что ты сказал)")

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

    def voices(self, profiles: list[dict]) -> None:
        pass

    def alert(self, text: str, key: str, active: bool) -> None:
        print(f"\n!!! {text} !!!\n" if active else f"({text})")

    def ring(self, text: str, key: str, active: bool) -> None:
        if active:
            print(f"\n>>> {text} (скажи «стоп») <<<\n")

    def enrollment(self, name: str, collected: int, needed: int, status: str) -> None:
        messages = {
            "started": f"Записываю голос «{name}»: прочитай вслух несколько фраз, с паузой после каждой.",
            "progress": f"   записано {collected} из {needed}",
            "done": f"Готово - голос «{name}» запомнен.",
            "partial": f"Время вышло - сохранил {collected} из {needed} записей голоса «{name}».",
            "failed": f"Не услышал речи - голос «{name}» не записан.",
            "cancelled": "Запись голоса отменена.",
        }
        print(messages.get(status, status))

    def ask_name(self, prompt: str, known: list[str] = ()) -> str:
        if known:
            print(f"(уже знаю голоса: {', '.join(known)} - то же имя добавит эту запись к голосу)")
        try:
            return self.ask(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            return ""
