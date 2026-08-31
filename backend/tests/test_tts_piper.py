from types import SimpleNamespace

import numpy as np

from app.tts.piper import chunks_to_audio_array


def chunk(values: list[float]) -> SimpleNamespace:
    return SimpleNamespace(audio_float_array=np.array(values, dtype=np.float32))


def test_concatenates_chunks_in_order():
    result = chunks_to_audio_array([chunk([1.0, 2.0]), chunk([3.0])])
    np.testing.assert_array_equal(result, np.array([1.0, 2.0, 3.0], dtype=np.float32))


def test_empty_chunk_list_returns_empty_array():
    result = chunks_to_audio_array([])
    assert result.size == 0


def test_single_chunk_passthrough():
    result = chunks_to_audio_array([chunk([5.0, 6.0, 7.0])])
    np.testing.assert_array_equal(result, np.array([5.0, 6.0, 7.0], dtype=np.float32))
