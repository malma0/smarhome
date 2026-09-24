"""Lets a front end follow what a TTS provider is playing - the desktop
window pulses its orb to the loudness of Jarvis's actual voice.

Providers that play raw audio themselves (voicebox, piper) call
announce(audio, sample_rate) right before playback; whoever registered via
set_listener gets a loudness envelope once, up front, and animates in sync
from there - no streaming during playback. Providers that hand playback to
something else (edge's mp3 via pygame, SAPI) don't announce; a front end
falls back to a generic speaking animation for those.
"""

from collections.abc import Callable

import numpy as np

ENVELOPE_FRAME_SECONDS = 0.05

_listener: Callable[[list[float], float], None] | None = None


def set_listener(listener: Callable[[list[float], float], None] | None) -> None:
    global _listener
    _listener = listener


def envelope(audio: np.ndarray, sample_rate: int, frame_seconds: float = ENVELOPE_FRAME_SECONDS) -> list[float]:
    """RMS loudness per frame, normalized so the loudest frame is 1.0.
    Accepts int or float samples, mono or multi-channel."""
    samples = np.asarray(audio, dtype=np.float64)
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    frame = max(1, int(sample_rate * frame_seconds))
    count = len(samples) // frame
    if count == 0:
        return []
    rms = np.sqrt(np.mean(samples[: count * frame].reshape(count, frame) ** 2, axis=1))
    peak = rms.max()
    if peak == 0:
        return [0.0] * count
    return [round(float(v), 3) for v in rms / peak]


def announce(audio: np.ndarray, sample_rate: int) -> None:
    listener = _listener
    if listener is None:
        return
    try:
        listener(envelope(audio, sample_rate), ENVELOPE_FRAME_SECONDS)
    except Exception:  # noqa: BLE001 - a UI hiccup must never stop the voice
        pass
