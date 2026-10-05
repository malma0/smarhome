"""The name, the way smart speakers hear it: a soft first stage that misses
nothing, a second one that throws out what wasn't the name.

  Vosk heard the name -> awake, as before (it's precise, just deaf to half the
  ways the name gets said). Otherwise the own model scores the phrase (~30 ms);
  over STAGE1_THRESHOLD - a level that catches ~97% of the names and plenty of
  other speech - Whisper, locally on the GPU, listens to it with the name as a
  hint and says whether it was there (~0.2 s). On the residents' recordings
  that check confirmed 137 of 139 names and none of 240 ordinary phrases.

Everything stays on the machine. With collect=True the phrases the first stage
flagged are kept with the verdict (wakeword/data/_field/) - real names and
real near-misses to retrain the own model on later.
"""

from __future__ import annotations

import threading
import time
import wave
from pathlib import Path

import numpy as np

from wakeword import detector

STAGE1_THRESHOLD = 0.9
MIN_SPEECH_SECONDS = 0.25
FIELD = Path(__file__).resolve().parent / "data" / "_field"


class Cascade:
    def __init__(self, stage1_threshold: float = STAGE1_THRESHOLD, collect: bool = False):
        self.stage1_threshold = stage1_threshold
        self.collect = collect
        self._model = None
        self._verifier = None
        self._ready = threading.Event()
        self.error: str | None = None

    def load(self) -> None:
        """The own model and Whisper - a few seconds; call it on a thread at startup."""
        try:
            from wakeword.verify import NameVerifier

            self._model = detector.WakeModel()
            self._verifier = NameVerifier("small")
        except Exception as exc:  # noqa: BLE001 - no model file, no GPU: Vosk alone, as before
            self.error = repr(exc)
        finally:
            self._ready.set()

    @property
    def ready(self) -> bool:
        return self._ready.is_set() and self.error is None

    def check(self, audio: np.ndarray, speech_seconds: float) -> tuple[bool, str, str]:
        """(the name was there, what Whisper heard, a note for the log) - for a phrase Vosk found no name in."""
        if not self.ready or speech_seconds < MIN_SPEECH_SECONDS:
            return False, "", ""
        started = time.perf_counter()
        score = self._model.score(audio)
        if score < self.stage1_threshold:
            return False, "", f"own model {score:.2f}"
        heard, text, seconds = self._verifier.check(audio)
        note = (f"own model {score:.2f} -> whisper {text!r} ({seconds:.2f}s) -> "
                f"{'NAME' if heard else 'not the name'}, {time.perf_counter() - started:.2f}s in all")
        if self.collect:
            self._keep(audio, "name" if heard else "not_name")
        return heard, text, note

    @staticmethod
    def _keep(audio: np.ndarray, verdict: str) -> None:
        folder = FIELD / verdict
        folder.mkdir(parents=True, exist_ok=True)
        with wave.open(str(folder / f"{time.strftime('%Y%m%dT%H%M%S')}_{len(list(folder.glob('*.wav'))):04d}.wav"),
                       "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(detector.RATE)
            w.writeframes(audio.astype(np.int16).tobytes())
