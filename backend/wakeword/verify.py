"""The second stage of the wake-word cascade: Whisper, locally on the GPU,
listens to the second or two the first stage flagged and says whether the
name was in it. The first stage (Vosk, or the own model on a soft threshold)
is tuned to miss nothing and wakes on plenty that isn't the name; this one
throws those out. Nothing leaves the machine.

    verifier = NameVerifier()                  # whisper "small", ~1 GB of VRAM, loaded once
    heard, text, seconds = verifier.check(audio)
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

MODELS = Path(__file__).resolve().parents[1] / "models" / "whisper"
HINT = "Джарвис."


def names_in(text: str, wake_words=("джарвис", "джервис"), max_edits: int = 1) -> bool:
    """A word in what Whisper heard that is the name - or one letter off it ("джарвиз")."""
    from app.hands_free import _edits

    words = "".join(c if c.isalnum() or c.isspace() else " " for c in text.casefold().replace("ё", "е")).split()
    return any(_edits(w, name) <= max_edits for w in words for name in wake_words)


class NameVerifier:
    def __init__(self, model: str = "small", device: str | None = None, max_edits: int = 1):
        import torch
        import whisper

        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model = whisper.load_model(model, device=self.device, download_root=str(MODELS))
        self.max_edits = max_edits

    def transcribe(self, audio: np.ndarray) -> str:
        x = audio.astype(np.float32) / 32768
        # the name as a hint: without it whisper-small wrote "Джаррис", "Джарбиус" and knew the name
        # in 2 takes of 99; with it, 137 of 139 - and still none of 240 ordinary phrases
        result = self.model.transcribe(x, language="ru", task="transcribe", temperature=0.0,
                                       condition_on_previous_text=False, without_timestamps=True,
                                       fp16=self.device == "cuda", initial_prompt=HINT)
        return result["text"].strip()

    def check(self, audio: np.ndarray) -> tuple[bool, str, float]:
        started = time.perf_counter()
        text = self.transcribe(audio)
        return names_in(text, max_edits=self.max_edits), text, time.perf_counter() - started
