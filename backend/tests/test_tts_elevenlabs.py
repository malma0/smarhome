import asyncio
import json

import httpx
import pytest

from app.tts.elevenlabs import ElevenLabsTTSProvider, synthesize


_RealAsyncClient = httpx.AsyncClient


def fake_transport(handler):
    # Use the real class here, not the (about to be) monkeypatched name -
    # otherwise this recurses into itself.
    return _RealAsyncClient(transport=httpx.MockTransport(handler))


def test_synthesize_posts_to_correct_voice_endpoint_with_auth_header(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = request.read()
        return httpx.Response(200, content=b"fake-mp3-bytes")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: fake_transport(handler))

    result = asyncio.run(synthesize("привет", voice_id="voice123", api_key="test-key"))

    assert result == b"fake-mp3-bytes"
    assert captured["url"] == "https://api.elevenlabs.io/v1/text-to-speech/voice123"
    assert captured["headers"]["xi-api-key"] == "test-key"
    assert json.loads(captured["body"])["text"] == "привет"


def test_synthesize_raises_on_http_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, content=b"unauthorized")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: fake_transport(handler))

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(synthesize("hi", voice_id="voice123", api_key="bad-key"))


def test_speak_plays_synthesized_audio(monkeypatch):
    monkeypatch.setattr("app.tts.elevenlabs.synthesize", lambda *a, **k: _async_bytes(b"abc"))
    played = []
    monkeypatch.setattr("app.tts.elevenlabs.play_mp3_bytes", lambda audio: played.append(audio))

    provider = ElevenLabsTTSProvider(api_key="k", voice_id="v")
    asyncio.run(provider.speak("hello"))

    assert played == [b"abc"]


def test_speak_warns_but_does_not_raise_past_soft_budget(monkeypatch, capsys):
    monkeypatch.setattr("app.tts.elevenlabs.synthesize", lambda *a, **k: _async_bytes(b"abc"))
    monkeypatch.setattr("app.tts.elevenlabs.play_mp3_bytes", lambda audio: None)

    provider = ElevenLabsTTSProvider(api_key="k", voice_id="v", monthly_char_budget=5)
    asyncio.run(provider.speak("this is way more than five characters"))

    assert "лимит" in capsys.readouterr().out


async def _async_bytes(data: bytes) -> bytes:
    return data
