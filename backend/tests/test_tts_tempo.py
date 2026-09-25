import numpy as np
import pytest

from app.tts.tempo import change_tempo

SR = 24000


def _tone(seconds: float, freq: float = 230) -> np.ndarray:
    return 0.5 * np.sin(2 * np.pi * freq * np.arange(int(seconds * SR)) / SR)


def _peak_hz(x: np.ndarray) -> float:
    return float(np.fft.rfftfreq(len(x), 1 / SR)[np.argmax(np.abs(np.fft.rfft(x)))])


@pytest.mark.parametrize("rate", [0.9, 1.1, 1.2])
def test_length_changes_by_the_rate(rate):
    assert len(change_tempo(_tone(3.0), SR, rate)) == int(3.0 * SR / rate)


def test_pitch_stays_the_same():
    """Unlike resampling, which would raise it by the same 20%."""
    assert abs(_peak_hz(change_tempo(_tone(2.0), SR, 1.2)) - 230) < 2


def test_no_clicks_on_a_steady_tone():
    """Pieces are aligned to the waveform, so a steady tone stays steady."""
    out = change_tempo(_tone(2.0), SR, 1.15)[SR // 10 : -SR // 10]
    envelope = np.abs(out[: len(out) // 240 * 240]).reshape(-1, 240).max(axis=1)
    assert envelope.min() > 0.45 and envelope.max() < 0.55


def test_rate_one_and_empty_input_are_untouched():
    x = _tone(0.5)
    assert change_tempo(x, SR, 1.0) is x
    assert len(change_tempo(np.zeros(0), SR, 1.2)) == 0
