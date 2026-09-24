"""PrerollBuffer and the VAD check are tested without any audio device -
MicRecorder itself (the sounddevice glue) needs a real microphone and is
left as manually-verified glue, same as the rest of voice_app.py's I/O."""

import numpy as np
import pytest

from app import audio_capture
from app.audio_capture import PrerollBuffer, UtteranceSegmenter, contains_speech, speech_seconds


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


# --- UtteranceSegmenter: frames are 1 (speech) or 0 (silence), and the
# fake VAD just reads that value. frame_seconds=0.1 keeps counts small:
# start after 2 speech frames, end after 3 silent ones, pre-roll 2 frames.


def _segmenter(**overrides):
    params = dict(
        is_speech=lambda f: bool(f[0, 0]),
        frame_seconds=0.1,
        preroll_seconds=0.2,
        start_seconds=0.2,
        end_silence_seconds=0.3,
        max_seconds=2.0,
    )
    params.update(overrides)
    return UtteranceSegmenter(**params)


def _feed_all(segmenter, pattern):
    """Feeds frames tagged with their position; returns finished phrases as
    lists of those positions."""
    phrases = []
    for i, bit in enumerate(pattern):
        frame = np.array([[bit], [i]], dtype=np.int16)
        phrase = segmenter.feed(frame)
        if phrase is not None:
            phrases.append([int(f[1, 0]) for f in phrase])
    return phrases


def test_a_phrase_is_cut_between_silences_with_its_preroll():
    #         0  1  2  3  4  5  6  7  8
    pattern = [0, 0, 1, 1, 1, 0, 0, 0, 0]
    [phrase] = _feed_all(_segmenter(preroll_seconds=0.4), pattern)
    # detected once frames 2-3 are speech; the 4-frame pre-roll reaches back
    # to frames 0-1 before that (in real use: a soft first syllable the VAD
    # didn't count yet); ends after 3 silent frames (5-7)
    assert phrase == [0, 1, 2, 3, 4, 5, 6, 7]


def test_a_single_click_does_not_open_a_phrase():
    pattern = [0, 1, 0, 0, 1, 0, 0, 0, 0, 0]  # isolated one-frame blips
    assert _feed_all(_segmenter(), pattern) == []


def test_a_short_pause_inside_a_phrase_does_not_split_it():
    pattern = [1, 1, 1, 0, 0, 1, 1, 0, 0, 0]  # 2-frame pause < 3-frame end
    assert len(_feed_all(_segmenter(), pattern)) == 1


def test_two_phrases_separated_by_enough_silence_come_out_separately():
    pattern = [1, 1, 1, 0, 0, 0, 0, 1, 1, 1, 0, 0, 0]
    assert len(_feed_all(_segmenter(), pattern)) == 2


def test_stray_noise_blips_during_the_pause_do_not_keep_a_phrase_open():
    """The bug found live: the VAD calls a frame of room hiss 'speech' now
    and then. Under an unbroken-silence rule each blip restarted the count
    and the phrase never ended; by proportion it ends normally."""
    segmenter = _segmenter(end_silence_seconds=1.0, end_ratio=0.8, max_seconds=100)
    pause_with_blips = [0, 0, 0, 0, 1] * 8  # a blip every 0.5s, never 10 silent frames in a row
    phrases = _feed_all(segmenter, [1, 1, 1] + pause_with_blips)
    assert len(phrases) == 1


def test_endless_speech_is_cut_at_max_length():
    phrases = _feed_all(_segmenter(max_seconds=1.0), [1] * 30)
    assert phrases and all(len(p) <= 10 for p in phrases)


def test_reset_drops_a_half_finished_phrase():
    segmenter = _segmenter()
    _feed_all(segmenter, [1, 1, 1])  # phrase in progress
    segmenter.reset()
    assert _feed_all(segmenter, [0, 0, 0, 0]) == []  # nothing left to finish


def test_mic_level_maps_quiet_room_to_0_and_close_speech_to_1():
    from app.audio_capture import mic_level

    def at_dbfs(db):
        amplitude = 32768 * 10 ** (db / 20)
        return np.full((480, 1), amplitude, dtype=np.float64)

    assert mic_level(at_dbfs(-70)) == 0.0
    assert mic_level(at_dbfs(-40)) == pytest.approx(0.5, abs=0.01)
    assert mic_level(at_dbfs(-10)) == 1.0
    assert mic_level(np.zeros((480, 1), dtype=np.int16)) == 0.0


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
