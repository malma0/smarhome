"""Cleans up cloned speech: rumble, hiss and breaths between words.

Chatterbox copies the reference clip whole - noise included. The ReyZi
clip comes from a YouTube video with a low hum (under 200 Hz, nearly as
loud as her voice there) and hiss above 8 kHz, and every reply came out
with the same noise and audible breaths. The same cleanup is used twice:
once on the reference clip, and optionally on every
reply before it's played (VOICEBOX_CLEANUP). Listened A/B: cleaned
reference + cleaned reply was the best of the three ReyZi variants - but
still not noise-free, and the clean audiobook voice won over it. For a clean
reference like that one this is off: it has nothing to fix there.

scipy only, and no pitch tracker: librosa's pyin took 1.7 s on a 6 s
reply, this takes ~0.1 s.
"""

import io
import wave

import numpy as np
from scipy.ndimage import uniform_filter1d, uniform_filter
from scipy.signal import butter, istft, sosfiltfilt, stft

LOW_CUT_HZ = 140  # her voice starts ~200 Hz (measured F0 5th percentile)
HIGH_CUT_HZ = 10000
N_FFT, HOP = 1024, 256
OVERSUBTRACT = 2.0
GAIN_FLOOR = 0.06  # -24 dB: deeper floors "gurgle" (musical noise)
VOICED_CORRELATION = 0.45
NEAR_VOICED_SECONDS = 0.08  # quiet consonants next to vowels are kept
CONSONANT_DB = 8  # unvoiced but this close to speech level: a consonant, kept
MAX_PAUSE, SHORT_PAUSE = 0.35, 0.30
PEAK_DBFS = -1.0


def _band_limit(x: np.ndarray, sr: int) -> np.ndarray:
    high = min(HIGH_CUT_HZ, 0.45 * sr)
    return sosfiltfilt(butter(4, [LOW_CUT_HZ, high], "bandpass", fs=sr, output="sos"), x)


def _spectral_gate(x: np.ndarray, sr: int) -> np.ndarray:
    _, _, spec = stft(x, fs=sr, nperseg=N_FFT, noverlap=N_FFT - HOP)
    mag = np.abs(spec)
    # Minimum statistics: speech this dense has almost no pauses to learn
    # the noise from, so per frequency the quietest 5% of frames stand in.
    noise = np.percentile(mag, 5, axis=1, keepdims=True)
    gain = np.clip(1 - (OVERSUBTRACT * noise / (mag + 1e-9)) ** 2, 0, 1) ** 0.5
    gain = np.maximum(uniform_filter(gain, size=(3, 5)), GAIN_FLOOR)
    _, y = istft(spec * gain, fs=sr, nperseg=N_FFT, noverlap=N_FFT - HOP)
    return y[: len(x)]


def _frames(x: np.ndarray, size: int) -> np.ndarray:
    """Frame i is centred on sample i * HOP."""
    x = np.pad(x, (size // 2, size // 2))
    count = max(1 + (len(x) - size) // HOP, 0)
    idx = np.arange(size)[None, :] + HOP * np.arange(count)[:, None]
    return x[idx]


def voiced_frames(x: np.ndarray, sr: int, fmin: float = 120, fmax: float = 600) -> np.ndarray:
    """Per HOP-frame: does it have a pitch? Normalized autocorrelation peak
    within the voice's pitch range."""
    size = 2048 if sr > 16000 else 1024
    frames = _frames(x, size) * np.hanning(size)
    power = np.abs(np.fft.rfft(frames, n=2 * size, axis=1)) ** 2
    corr = np.fft.irfft(power, axis=1)[:, :size]
    corr = corr / (corr[:, :1] + 1e-12)
    lo, hi = int(sr / fmax), int(sr / fmin)
    return corr[:, lo:hi].max(axis=1) > VOICED_CORRELATION


def _gate_between_words(x: np.ndarray, sr: int) -> np.ndarray:
    voiced = voiced_frames(x, sr)
    frames = _frames(x, N_FFT)[: len(voiced)]
    level = 10 * np.log10((frames**2).mean(axis=1) + 1e-12)
    if not voiced.any():
        return x
    speech_level = np.median(level[voiced])
    near = int(NEAR_VOICED_SECONDS * sr / HOP)
    near_voiced = np.convolve(voiced.astype(float), np.ones(2 * near + 1), "same") > 0
    keep = near_voiced | (level > speech_level - CONSONANT_DB)
    envelope = np.interp(np.arange(len(x)), np.arange(len(keep)) * HOP, keep.astype(float))
    return x * uniform_filter1d(envelope, size=int(0.02 * sr))  # 20 ms fades


def _shorten_pauses(x: np.ndarray, sr: int) -> np.ndarray:
    silent = np.abs(x) < 1e-4
    edges = np.flatnonzero(np.diff(np.r_[0, silent.astype(int), 0]))
    pieces, pos = [], 0
    for start, end in zip(edges[::2], edges[1::2]):
        pieces.append(x[pos:start])
        gap = end - start
        pieces.append(np.zeros(int(SHORT_PAUSE * sr) if gap > MAX_PAUSE * sr else gap))
        pos = end
    pieces.append(x[pos:])
    return np.concatenate(pieces)


def clean_speech(x: np.ndarray, sr: int) -> np.ndarray:
    """Float mono in, float mono out (peak at PEAK_DBFS)."""
    if len(x) < N_FFT * 2:
        return x
    y = _shorten_pauses(_gate_between_words(_spectral_gate(_band_limit(x, sr), sr), sr), sr)
    peak = np.max(np.abs(y))
    return y / peak * 10 ** (PEAK_DBFS / 20) if peak > 0 else y


def transform_wav_bytes(wav_bytes: bytes, transform) -> bytes:
    """16-bit WAV in and out; transform(float mono samples, rate) -> samples.
    Other sample widths come back untouched (Voicebox sends 16-bit)."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        rate, channels, width = wf.getframerate(), wf.getnchannels(), wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())
    if width != 2:
        return wav_bytes
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768
    if channels > 1:
        x = x.reshape(-1, channels).mean(axis=1)
    y = transform(x, rate)
    out = io.BytesIO()
    with wave.open(out, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes((np.clip(y, -1, 1) * 32767).astype(np.int16).tobytes())
    return out.getvalue()


def clean_wav_bytes(wav_bytes: bytes) -> bytes:
    return transform_wav_bytes(wav_bytes, clean_speech)
