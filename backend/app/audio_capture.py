"""Microphone capture for voice_app.py that doesn't lose the start or end of
a phrase, plus a voice-activity check so silence never reaches Whisper.

Start of phrase: the original version opened the mic only after the user
pressed Enter. Opening an audio device takes a noticeable fraction of a
second, and people start talking the instant they press the key, so the
first word regularly got cut ("Включи на нём видео" came through as "На нём
видео"). Now the mic stays open for the whole session and the last
PREROLL_SECONDS of audio are always kept in a small rolling buffer (memory
only, overwritten continuously, never saved): pressing Enter turns that
buffer into the start of the recording instead of starting from silence.

End of phrase: recording continues for TAIL_SECONDS after the stop key,
since people tend to press it while still finishing the last word.

Silence: Whisper hallucinates on silent or near-silent clips - verified
live, faint room hiss came back as "Спасибо." or "Дисклеймер" - and Groq's
Whisper reports no_speech_prob=0 even for pure silence, so its own flag
can't catch it. contains_speech() runs WebRTC's voice activity detector
(webrtcvad, already installed for speaker ID) before anything is sent.
"""

import collections
import threading
import time
from typing import Any

import numpy as np

BLOCK_SECONDS = 0.05
PREROLL_SECONDS = 0.5
TAIL_SECONDS = 0.3

# Measured on real audio: mode 2 flagged ~0.1s of "speech" in 2s of room
# hiss, but ~8s in 10s of genuinely quiet (-50 dB) speech. Mode 3 missed
# that quiet speech almost entirely (0.7s of 10s), so it's too strict here.
VAD_MODE = 2
VAD_FRAME_SECONDS = 0.03
MIN_SPEECH_SECONDS = 0.25


class PrerollBuffer:
    """The buffering logic on its own, no audio device involved - fed chunks
    from the mic callback (sounddevice's own thread) and read from the main
    thread, hence the lock."""

    def __init__(self, preroll_chunks: int):
        self._preroll: collections.deque[np.ndarray] = collections.deque(maxlen=preroll_chunks)
        self._recording: list[np.ndarray] | None = None
        self._lock = threading.Lock()

    def feed(self, chunk: np.ndarray) -> None:
        with self._lock:
            if self._recording is not None:
                self._recording.append(chunk)
            else:
                self._preroll.append(chunk)

    def begin(self) -> None:
        with self._lock:
            self._recording = list(self._preroll)
            self._preroll.clear()

    def end(self) -> list[np.ndarray]:
        with self._lock:
            frames = self._recording or []
            self._recording = None
            return frames


class MicRecorder:
    """Keeps one input stream open for the whole session. Windows shows the
    "microphone in use" indicator the entire time the app runs - that's
    this, the pre-roll buffer, not a recording being saved."""

    def __init__(self, sample_rate: int, preroll_seconds: float = PREROLL_SECONDS):
        import sounddevice as sd

        self._buffer = PrerollBuffer(round(preroll_seconds / BLOCK_SECONDS))
        self._stream = sd.InputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="int16",
            blocksize=int(sample_rate * BLOCK_SECONDS),
            callback=self._callback,
        )
        self._stream.start()

    def _callback(self, indata: np.ndarray, frame_count: int, time_info: Any, status: Any) -> None:
        self._buffer.feed(indata.copy())

    def begin(self) -> None:
        self._buffer.begin()

    def end(self, tail_seconds: float = TAIL_SECONDS) -> list[np.ndarray]:
        time.sleep(tail_seconds)
        return self._buffer.end()

    def close(self) -> None:
        self._stream.stop()
        self._stream.close()


def speech_seconds(frames: list[np.ndarray], sample_rate: int) -> float:
    """How much of the recording webrtcvad considers speech. webrtcvad only
    accepts 8/16/32/48 kHz and 10/20/30 ms frames of int16 mono."""
    import webrtcvad

    if not frames:
        return 0.0
    audio = np.concatenate(frames, axis=0).reshape(-1).astype(np.int16)
    vad = webrtcvad.Vad(VAD_MODE)
    frame_len = int(sample_rate * VAD_FRAME_SECONDS)
    speech_frames = sum(
        vad.is_speech(audio[i : i + frame_len].tobytes(), sample_rate)
        for i in range(0, len(audio) - frame_len + 1, frame_len)
    )
    return speech_frames * VAD_FRAME_SECONDS


def contains_speech(frames: list[np.ndarray], sample_rate: int) -> bool:
    return speech_seconds(frames, sample_rate) >= MIN_SPEECH_SECONDS
