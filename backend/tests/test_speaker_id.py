"""embed_wav_bytes (the only function backed by the real Resemblyzer model)
is intentionally not exercised here - it needs actual speech audio and a
~15MB pretrained model load, neither appropriate for a fast unit test.
Everything else (storage, matching) is pure numpy/json and tested directly.
"""

import numpy as np
import pytest

from app.db import connect
from app.memory import MemoryStore
from app.speaker_id import (
    cosine_similarity,
    embedding_from_json,
    embedding_to_json,
    enroll_resident,
    identify_resident,
    load_enrolled_voiceprints,
)


@pytest.fixture
def memory(tmp_path):
    return MemoryStore(connect(str(tmp_path / "test.db")))


def _vec(*values: float) -> np.ndarray:
    return np.array(values, dtype=np.float32)


def test_embedding_round_trips_through_json():
    original = _vec(0.1, -0.2, 0.3)
    restored = embedding_from_json(embedding_to_json(original))
    assert np.allclose(original, restored)


def test_cosine_similarity_of_identical_vectors_is_one():
    v = _vec(0.5, 0.5, 0.5)
    assert cosine_similarity(v, v) == pytest.approx(1.0)


def test_cosine_similarity_of_opposite_vectors_is_minus_one():
    assert cosine_similarity(_vec(1, 0), _vec(-1, 0)) == pytest.approx(-1.0)


def test_cosine_similarity_of_orthogonal_vectors_is_zero():
    assert cosine_similarity(_vec(1, 0), _vec(0, 1)) == pytest.approx(0.0)


def test_cosine_similarity_handles_a_zero_vector_without_dividing_by_zero():
    assert cosine_similarity(_vec(0, 0), _vec(1, 0)) == 0.0


def test_identify_resident_returns_best_match_above_threshold():
    target = _vec(1.0, 0.0)
    enrolled = {
        "matvei": _vec(0.99, 0.05),  # nearly identical direction
        "ivan": _vec(0.0, 1.0),  # orthogonal - clearly a different voice
    }

    assert identify_resident(target, enrolled) == "matvei"


def test_identify_resident_returns_none_below_threshold():
    target = _vec(1.0, 0.0)
    enrolled = {"ivan": _vec(0.0, 1.0)}  # nothing close enough

    assert identify_resident(target, enrolled) is None


def test_identify_resident_returns_none_when_nobody_enrolled():
    assert identify_resident(_vec(1.0, 0.0), {}) is None


def test_identify_resident_respects_a_custom_threshold():
    target = _vec(1.0, 0.0)
    enrolled = {"ivan": _vec(0.9, 0.1)}  # similarity ~0.99, but demand more

    assert identify_resident(target, enrolled, threshold=0.999) is None


def test_enroll_resident_stores_and_loads_back(memory):
    embedding = _vec(0.1, 0.2, 0.3)

    enroll_resident(memory, "matvei", embedding)

    enrolled = load_enrolled_voiceprints(memory)
    assert set(enrolled) == {"matvei"}
    assert np.allclose(enrolled["matvei"], embedding)


def test_load_enrolled_voiceprints_skips_residents_with_no_voice_enrolled(memory):
    memory.ensure_resident("guest")  # exists, but never enrolled a voice
    enroll_resident(memory, "matvei", _vec(0.1, 0.2))

    enrolled = load_enrolled_voiceprints(memory)

    assert set(enrolled) == {"matvei"}


def test_re_enrolling_overwrites_the_previous_embedding(memory):
    enroll_resident(memory, "matvei", _vec(1.0, 0.0))
    enroll_resident(memory, "matvei", _vec(0.0, 1.0))

    enrolled = load_enrolled_voiceprints(memory)

    assert np.allclose(enrolled["matvei"], _vec(0.0, 1.0))
