"""PrerollBuffer and the VAD check are tested without any audio device -
MicRecorder itself (the sounddevice glue) needs a real microphone and is
left as manually-verified glue, same as the rest of voice_app.py's I/O."""

import numpy as np

from app import audio_capture
from app.audio_capture import PrerollBuffer, contains_speech, speech_seconds


def _chunk(value: int, n: int = 4) -> np.ndarray:
    return np.full((n, 1), value, dtype=np.int16)


def test_audio_before_begin_is_kept_only_up_to_the_preroll_length():
    buffer = PrerollBuffer(preroll_chunks=2)
    for v in (1, 2, 3):
        buffer.feed(_chunk(v))

    buffer.begin()
    frames = buffer.end()

    assert [int(f[0, 0]) for f in frames] == [2, 3]  # oldest (1) rolled off


def test_recording_starts_with_the_preroll_then_everything_after_begin():
    """This is the fix for the first word getting cut: whatever was said
    just before the start key is at the front of the recording."""
    buffer = PrerollBuffer(preroll_chunks=2)
    buffer.feed(_chunk(1))
    buffer.feed(_chunk(2))

    buffer.begin()
    for v in (3, 4, 5):
        buffer.feed(_chunk(v))
    frames = buffer.end()

    assert [int(f[0, 0]) for f in frames] == [1, 2, 3, 4, 5]


def test_after_end_audio_goes_back_to_the_rolling_preroll():
    buffer = PrerollBuffer(preroll_chunks=1)
    buffer.begin()
    buffer.feed(_chunk(1))
    buffer.end()

    buffer.feed(_chunk(2))
    buffer.feed(_chunk(3))
    buffer.begin()
    frames = buffer.end()

    assert [int(f[0, 0]) for f in frames] == [3]  # nothing from the previous recording leaks in


def test_end_without_begin_returns_nothing():
    buffer = PrerollBuffer(preroll_chunks=2)
    buffer.feed(_chunk(1))
    assert buffer.end() == []


def test_pure_silence_is_not_speech():
    frames = [np.zeros((16000, 1), dtype=np.int16)]  # 1s of digital silence
    assert speech_seconds(frames, 16000) == 0.0
    assert not contains_speech(frames, 16000)


def test_no_frames_is_not_speech():
    assert not contains_speech([], 16000)


def test_speech_seconds_counts_30ms_frames_marked_as_speech(monkeypatch):
    """Frame slicing checked with a fake VAD that calls anything non-zero
    speech - the real classifier's accuracy was measured on real audio
    instead (see VAD_MODE in audio_capture.py)."""

    class _FakeVad:
        def __init__(self, mode):
            pass

        def is_speech(self, frame_bytes, sample_rate):
            return any(frame_bytes)

    monkeypatch.setattr("webrtcvad.Vad", _FakeVad)
    frame = int(16000 * audio_capture.VAD_FRAME_SECONDS)  # 480 samples
    loud = np.ones((frame * 10, 1), dtype=np.int16)  # 10 frames = 0.3s
    quiet = np.zeros((frame * 5, 1), dtype=np.int16)

    assert speech_seconds([quiet, loud, quiet], 16000) == 10 * audio_capture.VAD_FRAME_SECONDS
    assert contains_speech([quiet, loud, quiet], 16000)
    assert not contains_speech([quiet, loud[: frame * 5], quiet], 16000)  # 0.15s < MIN_SPEECH_SECONDS
