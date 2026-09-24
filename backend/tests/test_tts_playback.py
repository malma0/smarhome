import numpy as np
import pytest

from app.tts import playback


@pytest.fixture(autouse=True)
def _no_listener_leak():
    yield
    playback.set_listener(None)


def test_envelope_is_one_value_per_frame_normalized_to_the_loudest():
    sr = 1000  # 50 samples per 0.05s frame
    quiet = np.full(50, 100, dtype=np.int16)
    loud = np.full(50, 400, dtype=np.int16)
    env = playback.envelope(np.concatenate([quiet, loud, quiet]), sr)
    assert env == [0.25, 1.0, 0.25]


def test_envelope_of_silence_is_zeros_not_a_division_error():
    assert playback.envelope(np.zeros(200), 1000) == [0.0, 0.0, 0.0, 0.0]


def test_envelope_downmixes_stereo_and_handles_short_audio():
    stereo = np.ones((100, 2), dtype=np.float32)
    assert playback.envelope(stereo, 1000) == [1.0, 1.0]
    assert playback.envelope(np.ones(10), 1000) == []  # shorter than one frame


def test_announce_hands_the_envelope_to_the_listener():
    received = []
    playback.set_listener(lambda levels, frame_seconds: received.append((levels, frame_seconds)))

    playback.announce(np.full(100, 7, dtype=np.int16), 1000)

    assert received == [([1.0, 1.0], playback.ENVELOPE_FRAME_SECONDS)]


def test_announce_without_a_listener_is_a_no_op():
    playback.announce(np.ones(100), 1000)  # must not raise


def test_a_failing_listener_never_breaks_playback():
    def _boom(levels, frame_seconds):
        raise RuntimeError("window closed")

    playback.set_listener(_boom)
    playback.announce(np.ones(100), 1000)  # must not raise
