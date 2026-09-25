"""Faster (or slower) speech without changing the pitch.

Voicebox has no speed setting, and the audiobook voice (hanna_sample2_30s)
reads at an audiobook pace - "как будто чуть чуть медленно". Resampling
would raise the pitch (the chipmunk effect), so replies are time-stretched
with WSOLA: the audio is cut into overlapping 40 ms pieces that are laid
back down closer together, each piece nudged by up to 10 ms so its waveform
lines up with the previous one - no phase smearing like a phase vocoder.
"""

import re

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


# A reply's last sentence ("Что-нибудь ещё?") gets its own, gentler speed:
# at one speed for the whole reply, the start dragged at 1.15 while the
# closing question already felt rushed at 1.2.
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
PAUSE_DB = 25  # this far under the speech level counts as a pause
MIN_PAUSE_SECONDS = 0.08
SEARCH_SECONDS = 0.8  # how far from the estimate a pause may be


def last_sentence_share(text: str) -> float:
    """Share of the text taken by the last sentence - where it starts in the
    audio, roughly. 0 for a single sentence (nothing to split off)."""
    sentences = [s for s in _SENTENCE_END.split(text.strip()) if s]
    if len(sentences) < 2:
        return 0.0
    return len(sentences[-1]) / len(" ".join(sentences))


def find_pause(x: np.ndarray, sr: int, near: int) -> int | None:
    """Middle of the pause closest to sample `near`, within SEARCH_SECONDS."""
    hop = int(0.01 * sr)
    frames = x[: len(x) // hop * hop].reshape(-1, hop)
    level = 10 * np.log10((frames**2).mean(axis=1) + 1e-12)
    quiet = level < np.median(level) - PAUSE_DB
    edges = np.flatnonzero(np.diff(np.r_[0, quiet.astype(int), 0]))
    best = None
    for start, end in zip(edges[::2], edges[1::2]):
        if (end - start) * hop < MIN_PAUSE_SECONDS * sr:
            continue
        middle = (start + end) // 2 * hop
        if abs(middle - near) <= SEARCH_SECONDS * sr and (best is None or abs(middle - near) < abs(best - near)):
            best = middle
    return best


def change_tempo_with_ending(x: np.ndarray, sr: int, rate: float, ending_rate: float, ending_share: float) -> np.ndarray:
    """`rate` up to the last sentence, `ending_rate` for it - cut in the
    pause before it. No clear pause there: one speed for everything."""
    if ending_share <= 0 or ending_rate == rate:
        return change_tempo(x, sr, rate)
    cut = find_pause(x, sr, int(len(x) * (1 - ending_share)))
    if cut is None:
        return change_tempo(x, sr, rate)
    return np.concatenate([change_tempo(x[:cut], sr, rate), change_tempo(x[cut:], sr, ending_rate)])
