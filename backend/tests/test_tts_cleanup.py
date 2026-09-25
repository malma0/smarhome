import io
import wave

import numpy as np

from app.tts.cleanup import clean_speech, clean_wav_bytes, voiced_frames

SR = 24000
rng = np.random.default_rng(0)


def _vowel(seconds: float, f0: float = 230) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return sum(0.3 / k * np.sin(2 * np.pi * f0 * k * t) for k in range(1, 8))


def _band_db(x: np.ndarray, lo: float, hi: float) -> float:
    spec = np.abs(np.fft.rfft(x)) ** 2
    freqs = np.fft.rfftfreq(len(x), 1 / SR)
    return 10 * np.log10(spec[(freqs >= lo) & (freqs < hi)].sum() + 1e-12)


def _phrase():
    """word - breath - word, over a 60 Hz hum and hiss, like the clone."""
    word, breath = _vowel(0.8), 0.02 * rng.standard_normal(int(0.35 * SR))
    clean = np.concatenate([word, np.zeros_like(breath), word])
    noisy = np.concatenate([word, breath, word])
    t = np.arange(len(noisy)) / SR
    noisy = noisy + 0.05 * np.sin(2 * np.pi * 60 * t) + 0.003 * rng.standard_normal(len(noisy))
    return clean, noisy, len(word)


def test_hum_goes_voice_stays():
    _, noisy, _ = _phrase()
    out = clean_speech(noisy, SR)
    scale = np.max(np.abs(out)) / np.max(np.abs(noisy))  # output is peak-normalized
    assert _band_db(out, 40, 80) - _band_db(noisy * scale, 40, 80) < -30
    assert abs(_band_db(out, 200, 2000) - _band_db(noisy * scale, 200, 2000)) < 3


def test_breath_between_words_is_muted():
    _, noisy, word = _phrase()
    out = clean_speech(noisy, SR)
    middle = out[word + int(0.15 * SR) : word + int(0.2 * SR)]  # the middle of the breath
    assert np.sqrt(np.mean(middle**2)) < 0.01 * np.max(np.abs(out))


def test_a_long_pause_is_shortened():
    word = _vowel(0.8)
    x = np.concatenate([word, np.zeros(SR), word])  # a 1 s pause
    out = clean_speech(x, SR)
    assert len(out) < len(x) - 0.5 * SR


def test_voiced_detection_tells_vowels_from_noise():
    assert voiced_frames(_vowel(1.0), SR)[5:-10].mean() > 0.95
    assert voiced_frames(0.1 * rng.standard_normal(SR), SR).mean() < 0.05


def _wav(samples: np.ndarray, width: int = 2) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(width)
        wf.setframerate(SR)
        dtype = {1: np.uint8, 2: np.int16, 4: np.int32}[width]
        wf.writeframes((samples * (2 ** (8 * width - 1) - 1) + (128 if width == 1 else 0)).astype(dtype).tobytes())
    return out.getvalue()


def test_wav_round_trip_keeps_format():
    _, noisy, _ = _phrase()
    result = clean_wav_bytes(_wav(noisy * 0.5))
    with wave.open(io.BytesIO(result), "rb") as wf:
        assert (wf.getframerate(), wf.getnchannels(), wf.getsampwidth()) == (SR, 1, 2)
        assert abs(wf.getnframes() - len(noisy)) < 0.05 * SR


def test_other_sample_widths_are_left_alone():
    original = _wav(_vowel(0.5) * 0.5, width=4)
    assert clean_wav_bytes(original) == original
