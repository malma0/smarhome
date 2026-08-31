"""Same idea as llm/base.py, for the output side of the voice loop: swap
which engine speaks Jarvis's replies without touching voice_app.py. Two
implementations exist today - edge.py (better quality, needs internet,
free) and sapi.py (offline, lower quality, zero dependencies beyond what's
already installed) - and voice_app.py falls back from the former to the
latter if synthesis fails.
"""

from typing import Protocol


class TTSProvider(Protocol):
    async def speak(self, text: str) -> None:
        """Synthesize and play text through the default output device."""
        ...
