"""embed_wav_bytes (the only function backed by the real Resemblyzer model)
is intentionally not exercised here - it needs actual speech audio and a
~15MB pretrained model load, neither appropriate for a fast unit test.
Everything else (storage, matching) is pure numpy/json and tested directly.
"""

import json

import numpy as np
import pytest

from app import speaker_id
from app.db import connect
from app.memory import MemoryStore
from app.speaker_id import (
    MAX_ENROLLED_SAMPLES,
    VOICEPRINT_PREFERENCE_KEY,
    cosine_similarity,
    decide,
    enroll_resident,
    identify_resident,
    load_enrolled_voiceprints,
    rank_residents,
)


@pytest.fixture
def memory(tmp_path):
    return MemoryStore(connect(str(tmp_path / "test.db")))


def _vec(*values: float) -> np.ndarray:
    return np.array(values, dtype=np.float32)


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
        "matvei": [_vec(0.99, 0.05)],  # nearly identical direction
        "ivan": [_vec(0.0, 1.0)],  # orthogonal - clearly a different voice
    }

    assert identify_resident(target, enrolled) == "matvei"


def test_identify_resident_returns_none_below_threshold():
    target = _vec(1.0, 0.0)
    enrolled = {"ivan": [_vec(0.0, 1.0)]}  # nothing close enough

    assert identify_resident(target, enrolled) is None


def test_identify_resident_returns_none_when_nobody_enrolled():
    assert identify_resident(_vec(1.0, 0.0), {}) is None


def test_identify_resident_respects_a_custom_threshold():
    target = _vec(1.0, 0.0)
    enrolled = {"ivan": [_vec(0.9, 0.1)]}  # similarity ~0.99, but demand more

    assert identify_resident(target, enrolled, threshold=0.999) is None


def test_identify_resident_matches_against_the_best_of_several_samples():
    """One noisy/atypical enrolled sample shouldn't sink a resident's match
    if another of their samples is a close hit - matching takes the best
    across all stored samples, not an average of them."""
    target = _vec(1.0, 0.0)
    enrolled = {"matvei": [_vec(0.0, 1.0), _vec(0.99, 0.05)]}  # one bad, one great

    assert identify_resident(target, enrolled) == "matvei"


def test_decide_confident_above_threshold():
    assert decide([("matvei", 0.78), ("elya", 0.40)], threshold=0.70) == ("matvei", True)


def test_decide_picks_the_clearly_closer_voice_without_confidence():
    """The real short-phrase case: 0.60 to his own profile, 0.45 to a
    female voice - under the threshold, but clearly him."""
    assert decide([("matvei", 0.60), ("elya", 0.45)], threshold=0.70) == ("matvei", False)


def test_decide_refuses_a_coin_toss_between_close_candidates():
    assert decide([("matvei", 0.60), ("elya", 0.55)], threshold=0.70) == (None, False)


def test_decide_needs_two_voices_to_compare():
    assert decide([("matvei", 0.60)], threshold=0.70) == (None, False)


def test_decide_rejects_a_voice_unlike_anyone():
    assert decide([("matvei", 0.40), ("elya", 0.20)], threshold=0.70) == (None, False)


def test_decide_with_nobody_enrolled():
    assert decide([], threshold=0.70) == (None, False)


def test_rank_orders_by_each_residents_best_sample():
    target = _vec(1.0, 0.0)
    ranked = rank_residents(target, {"a": [_vec(0.0, 1.0), _vec(1.0, 0.1)], "b": [_vec(0.7, 0.7)]})
    assert [r for r, _ in ranked] == ["a", "b"]


def test_enroll_resident_stores_and_loads_back(memory):
    embedding = _vec(0.1, 0.2, 0.3)

    enroll_resident(memory, "matvei", embedding)

    enrolled = load_enrolled_voiceprints(memory)
    assert set(enrolled) == {"matvei"}
    assert len(enrolled["matvei"]) == 1
    assert np.allclose(enrolled["matvei"][0], embedding)


def test_load_enrolled_voiceprints_skips_residents_with_no_voice_enrolled(memory):
    memory.ensure_resident("guest")  # exists, but never enrolled a voice
    enroll_resident(memory, "matvei", _vec(0.1, 0.2))

    enrolled = load_enrolled_voiceprints(memory)

    assert set(enrolled) == {"matvei"}


def test_re_enrolling_adds_a_sample_without_discarding_previous_ones(memory):
    enroll_resident(memory, "matvei", _vec(1.0, 0.0))
    enroll_resident(memory, "matvei", _vec(0.0, 1.0))

    enrolled = load_enrolled_voiceprints(memory)

    assert len(enrolled["matvei"]) == 2
    assert np.allclose(enrolled["matvei"][0], _vec(1.0, 0.0))
    assert np.allclose(enrolled["matvei"][1], _vec(0.0, 1.0))


def test_enrollment_caps_at_max_samples_dropping_the_oldest_first(memory):
    for i in range(MAX_ENROLLED_SAMPLES + 3):
        enroll_resident(memory, "matvei", _vec(float(i), 0.0))

    enrolled = load_enrolled_voiceprints(memory)

    assert len(enrolled["matvei"]) == MAX_ENROLLED_SAMPLES
    # the oldest (0, 1, 2) were dropped - the most recent ones survive
    first_values = [sample[0] for sample in enrolled["matvei"]]
    assert first_values == [float(i) for i in range(3, MAX_ENROLLED_SAMPLES + 3)]


# --- models, and the audio behind every sample ---


def test_each_model_keeps_its_own_profiles(memory):
    """Embeddings from different models aren't comparable - switching the
    model must not match against the other model's samples."""
    enroll_resident(memory, "Матвей", _vec(1.0, 0.0))  # resemblyzer (the default)
    speaker_id.configure("ecapa")
    assert load_enrolled_voiceprints(memory) == {}
    enroll_resident(memory, "Матвей", _vec(0.0, 1.0))
    assert np.allclose(load_enrolled_voiceprints(memory)["Матвей"][0], _vec(0.0, 1.0))
    speaker_id.configure("resemblyzer")
    assert np.allclose(load_enrolled_voiceprints(memory)["Матвей"][0], _vec(1.0, 0.0))


def test_decide_uses_the_active_models_thresholds():
    ranked = [("matvei", 0.55), ("elya", 0.20)]
    assert decide(ranked) == ("matvei", False)  # resemblyzer: 0.55 < 0.70, but clearly closer
    speaker_id.configure("ecapa")
    assert decide(ranked) == ("matvei", True)  # ecapa: 0.55 >= 0.50


def test_unknown_model_is_rejected():
    with pytest.raises(ValueError):
        speaker_id.configure("magic")


def test_sample_audio_is_saved_per_resident_and_listed_back(tmp_path):
    root = tmp_path / "vp"
    speaker_id.save_sample_audio("Матвей", b"RIFF-a", root)
    speaker_id.save_sample_audio("Эля", b"RIFF-b", root)
    speaker_id.save_sample_audio("Матвей", b"RIFF-c", root)

    audio = speaker_id.sample_audio(root)

    assert set(audio) == {"Матвей", "Эля"}
    assert [p.read_bytes() for p in audio["Матвей"]] == [b"RIFF-a", b"RIFF-c"]


def test_resident_names_cannot_escape_the_voiceprints_folder(tmp_path):
    path = speaker_id.save_sample_audio("../../evil", b"RIFF", tmp_path / "vp")
    assert (tmp_path / "vp") in path.parents


def test_rebuild_profiles_reembeds_saved_audio_with_the_active_model(memory, tmp_path, monkeypatch):
    root = tmp_path / "vp"
    speaker_id.save_sample_audio("Матвей", b"one", root)
    speaker_id.save_sample_audio("Матвей", b"two", root)
    monkeypatch.setattr(speaker_id, "embed_wav_bytes", lambda wav: _vec(float(len(wav)), 1.0))
    speaker_id.configure("ecapa")

    assert speaker_id.rebuild_profiles(memory, root) == {"Матвей": 2}
    assert len(load_enrolled_voiceprints(memory)["Матвей"]) == 2


def test_legacy_single_embedding_format_still_loads(memory):
    """The very first version of this module stored one flat embedding
    (not a list of samples) - old profiles must keep working, not get
    silently dropped by a schema change."""
    memory.ensure_resident("matvei")
    memory.set_preference("matvei", VOICEPRINT_PREFERENCE_KEY, json.dumps([0.1, 0.2, 0.3]))

    enrolled = load_enrolled_voiceprints(memory)

    assert len(enrolled["matvei"]) == 1
    assert np.allclose(enrolled["matvei"][0], _vec(0.1, 0.2, 0.3))
