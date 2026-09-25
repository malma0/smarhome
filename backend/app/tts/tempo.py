"""Faster (or slower) speech without changing the pitch.

Voicebox has no speed setting, and the audiobook voice (hanna_sample2_30s)
reads at an audiobook pace - "как будто чуть чуть медленно". Resampling
would raise the pitch (the chipmunk effect), so replies are time-stretched
with WSOLA: the audio is cut into overlapping 40 ms pieces that are laid
back down closer together, each piece nudged by up to 10 ms so its waveform
lines up with the previous one - no phase smearing like a phase vocoder.
"""

import numpy as np
from scipy.signal import correlate

FRAME_SECONDS = 0.04
TOLERANCE_SECONDS = 0.01


def change_tempo(x: np.ndarray, sr: int, rate: float) -> np.ndarray:
    """rate > 1 is faster: 1.15 makes the audio 1.15x shorter."""
    if rate == 1.0 or len(x) == 0:
        return x
    frame = int(FRAME_SECONDS * sr) // 2 * 2
    synthesis_hop = frame // 2
    analysis_hop = synthesis_hop * rate
    tolerance = int(TOLERANCE_SECONDS * sr)
    window = np.hanning(frame)

    padded = np.pad(x, (tolerance, frame + tolerance + synthesis_hop))
    out_len = int(len(x) / rate)
    out = np.zeros(out_len + frame)
    weight = np.zeros(out_len + frame)

    previous = 0  # where the last piece was taken from (in `padded`)
    for k in range(out_len // synthesis_hop + 1):
        nominal = int(k * analysis_hop) + tolerance
        if nominal + tolerance + frame > len(padded):
            break
        if k == 0:
            position = nominal
        else:
            # What would naturally follow the previous piece...
            follow = padded[previous + synthesis_hop : previous + synthesis_hop + frame]
            # ...and where near the nominal spot the audio looks most like it.
            region = padded[nominal - tolerance : nominal + tolerance + frame]
            position = nominal - tolerance + int(np.argmax(correlate(region, follow, mode="valid", method="fft")))
        start = k * synthesis_hop
        out[start : start + frame] += padded[position : position + frame] * window
        weight[start : start + frame] += window
        previous = position

    weight[weight < 1e-3] = 1.0
    return (out / weight)[:out_len]
