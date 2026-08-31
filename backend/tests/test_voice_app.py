"""Only the pure/HTTP pieces of voice_app.py are covered here - actual
microphone recording (record_until_enter) needs a real audio device and
isn't something CI can exercise, so it's left as manually-verified glue."""

import asyncio
import wave
from io import BytesIO
from unittest.mock import AsyncMock

import httpx
import numpy as np

from voice_app import frames_to_wav_bytes, transcribe


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
