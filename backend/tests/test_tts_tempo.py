import numpy as np
import pytest

from app.tts.tempo import change_tempo, change_tempo_with_ending, find_pause, last_sentence_share

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


def test_last_sentence_share():
    text = "Хорошо, Матвей. Я включила свет на кухне. Что-нибудь ещё?"
    assert abs(last_sentence_share(text) - len("Что-нибудь ещё?") / len(text)) < 0.01
    assert last_sentence_share("Включаю свет.") == 0.0
    assert last_sentence_share("Готово! Что-нибудь ещё?") > 0


def _two_sentences(pause_at: float = 3.0):
    """Speech-like tone, a 0.2 s pause, then a 1 s 'last sentence'."""
    return np.concatenate([_tone(pause_at), np.zeros(int(0.2 * SR)), _tone(1.0)])


def test_the_pause_nearest_the_estimate_is_found():
    x = _two_sentences()
    cut = find_pause(x, SR, near=int(3.3 * SR))
    assert 3.0 * SR < cut < 3.2 * SR
    assert find_pause(x, SR, near=int(1.0 * SR)) is None  # too far from any pause


def test_the_ending_gets_its_own_speed():
    x = _two_sentences()
    out = change_tempo_with_ending(x, SR, 1.2, 1.1, ending_share=1.2 / 4.2)
    expected = 3.1 / 1.2 + 1.1 / 1.1  # cut in the middle of the pause
    assert abs(len(out) / SR - expected) < 0.03


def test_no_pause_near_the_estimate_means_one_speed():
    x = _tone(4.0)
    out = change_tempo_with_ending(x, SR, 1.2, 1.1, ending_share=0.25)
    assert len(out) == int(4.0 * SR / 1.2)
