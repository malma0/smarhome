"""Only the pure/HTTP pieces of voice_app.py are covered here - actual
microphone recording (record_until_enter) needs a real audio device and
isn't something CI can exercise, so it's left as manually-verified glue."""

import asyncio
import wave
from io import BytesIO
from unittest.mock import AsyncMock, Mock

import httpx
import numpy as np
import pytest

from app.db import connect
from app.memory import MemoryStore
from voice_app import frames_to_wav_bytes, identify_or_enroll_speaker, transcribe


def test_empty_frames_produce_empty_bytes():
    assert frames_to_wav_bytes([]) == b""


def test_frames_produce_a_valid_wav_file():
    frames = [np.zeros((8000, 1), dtype=np.int16), np.ones((8000, 1), dtype=np.int16)]

    wav_bytes = frames_to_wav_bytes(frames, sample_rate=16000)

    with wave.open(BytesIO(wav_bytes), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 16000
        assert wf.getnframes() == 16000


def test_transcribe_posts_multipart_and_returns_stripped_text(monkeypatch):
    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"text": "  привет джарвис  "}

    mock_post = AsyncMock(return_value=_Resp())
    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    result = asyncio.run(transcribe(b"fake-wav-bytes", api_key="key", base_url="https://api.groq.com/openai/v1"))

    assert result == "привет джарвис"
    sent_kwargs = mock_post.call_args.kwargs
    assert sent_kwargs["data"]["model"] == "whisper-large-v3-turbo"
    assert sent_kwargs["files"]["file"][0] == "speech.wav"


# --- identify_or_enroll_speaker (app.speaker_id itself is mocked - no real
# embedding model or audio involved) ---


@pytest.fixture
def memory(tmp_path):
    return MemoryStore(connect(str(tmp_path / "test.db")))


def test_confident_match_is_returned_without_prompting(memory, monkeypatch):
    monkeypatch.setattr("voice_app.speaker_id.embed_wav_bytes", lambda wav: "embedding")
    monkeypatch.setattr("voice_app.speaker_id.load_enrolled_voiceprints", lambda mem: {"matvei": ["known"]})
    monkeypatch.setattr("voice_app.speaker_id.identify_resident", lambda emb, enrolled, threshold: "matvei")
    monkeypatch.setattr("voice_app.speaker_id.enroll_resident", Mock())
    prompt = Mock()

    result = identify_or_enroll_speaker(memory, b"wav", "default", 0.75, prompt_for_name=prompt)

    assert result == "matvei"
    prompt.assert_not_called()


def test_confident_match_also_feeds_the_recording_back_into_the_profile(memory, monkeypatch):
    """A successful match isn't just returned - it's fed back into
    speaker_id.enroll_resident so the profile keeps absorbing real usage
    instead of staying frozen at the first one-shot enrollment."""
    monkeypatch.setattr("voice_app.speaker_id.embed_wav_bytes", lambda wav: "embedding")
    monkeypatch.setattr("voice_app.speaker_id.load_enrolled_voiceprints", lambda mem: {"matvei": ["known"]})
    monkeypatch.setattr("voice_app.speaker_id.identify_resident", lambda emb, enrolled, threshold: "matvei")
    enroll = Mock()
    monkeypatch.setattr("voice_app.speaker_id.enroll_resident", enroll)

    identify_or_enroll_speaker(memory, b"wav", "default", 0.75, prompt_for_name=Mock())

    enroll.assert_called_once_with(memory, "matvei", "embedding")


def test_unrecognized_voice_enrolls_under_the_given_name(memory, monkeypatch):
    monkeypatch.setattr("voice_app.speaker_id.embed_wav_bytes", lambda wav: "embedding")
    monkeypatch.setattr("voice_app.speaker_id.load_enrolled_voiceprints", lambda mem: {})
    monkeypatch.setattr("voice_app.speaker_id.identify_resident", lambda emb, enrolled, threshold: None)
    enrolled_calls = []
    monkeypatch.setattr(
        "voice_app.speaker_id.enroll_resident",
        lambda mem, resident_id, emb: enrolled_calls.append((resident_id, emb)),
    )

    result = identify_or_enroll_speaker(
        memory, b"wav", "default", 0.75, prompt_for_name=lambda _prompt: "matvei"
    )

    assert result == "matvei"
    assert enrolled_calls == [("matvei", "embedding")]


def test_unrecognized_voice_declining_to_enroll_falls_back_to_default(memory, monkeypatch):
    monkeypatch.setattr("voice_app.speaker_id.embed_wav_bytes", lambda wav: "embedding")
    monkeypatch.setattr("voice_app.speaker_id.load_enrolled_voiceprints", lambda mem: {})
    monkeypatch.setattr("voice_app.speaker_id.identify_resident", lambda emb, enrolled, threshold: None)
    enroll = Mock()
    monkeypatch.setattr("voice_app.speaker_id.enroll_resident", enroll)

    result = identify_or_enroll_speaker(memory, b"wav", "default", 0.75, prompt_for_name=lambda _prompt: "")

    assert result == "default"
    enroll.assert_not_called()


def test_a_broken_embedding_step_falls_back_to_default_without_crashing(memory, monkeypatch):
    def _raise(wav):
        raise RuntimeError("model not installed")

    monkeypatch.setattr("voice_app.speaker_id.embed_wav_bytes", _raise)
    prompt = Mock()

    result = identify_or_enroll_speaker(memory, b"wav", "default", 0.75, prompt_for_name=prompt)

    assert result == "default"
    prompt.assert_not_called()


def test_declining_via_eof_falls_back_to_default(memory, monkeypatch):
    monkeypatch.setattr("voice_app.speaker_id.embed_wav_bytes", lambda wav: "embedding")
    monkeypatch.setattr("voice_app.speaker_id.load_enrolled_voiceprints", lambda mem: {})
    monkeypatch.setattr("voice_app.speaker_id.identify_resident", lambda emb, enrolled, threshold: None)

    def _eof(_prompt):
        raise EOFError

    result = identify_or_enroll_speaker(memory, b"wav", "default", 0.75, prompt_for_name=_eof)

    assert result == "default"
