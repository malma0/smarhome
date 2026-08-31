"""Offline TTS via the OS's own SAPI voices (pyttsx3). Lower quality than
EdgeTTSProvider - this exists as the dependency-free, no-internet fallback,
not the recommended default."""

import asyncio
from typing import Any


def pick_voice_id(voices: list[Any], language_hint: str = "ru") -> str | None:
    """Pure and testable: picks the first installed voice matching a
    language hint, checking both the voice id and its declared languages
    (different SAPI drivers populate these inconsistently)."""
    for voice in voices:
        languages = [str(lang).lower() for lang in getattr(voice, "languages", [])]
        if language_hint in voice.id.lower() or any(language_hint in lang for lang in languages):
            return voice.id
    return None


class SapiTTSProvider:
    def __init__(self, language_hint: str = "ru"):
        import pyttsx3

        self._engine = pyttsx3.init()
        voice_id = pick_voice_id(self._engine.getProperty("voices"), language_hint)
        if voice_id:
            self._engine.setProperty("voice", voice_id)

    async def speak(self, text: str) -> None:
        # pyttsx3 blocks the calling thread - run it off the event loop so a
        # long reply doesn't freeze the rest of the async app.
        await asyncio.to_thread(self._speak_sync, text)

    def _speak_sync(self, text: str) -> None:
        self._engine.say(text)
        self._engine.runAndWait()
