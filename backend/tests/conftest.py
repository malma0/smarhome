import pytest

from app import speaker_id


@pytest.fixture(autouse=True)
def _isolate_speaker_id(tmp_path, monkeypatch):
    """Every test gets its own voiceprints folder - saved voice samples must
    never land in the real backend/voiceprints/ (residents' own voices) -
    and starts from the default speaker model, whatever a previous test
    configured."""
    monkeypatch.setattr(speaker_id, "VOICEPRINTS_DIR", tmp_path / "voiceprints")
    previous = speaker_id.active_model().name
    speaker_id.configure("resemblyzer")
    yield
    speaker_id.configure(previous)
