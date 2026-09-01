"""No real Voicebox server is touched in these tests - httpx calls go
through a fake ASGI-less transport (httpx.MockTransport) that scripts the
exact three-call sequence (POST /speak, GET /generate/{id}/status,
GET /audio/{id}) the real local server is expected to follow."""

import asyncio

import httpx
import pytest

from app.tts.voicebox import VoiceboxTTSProvider, synthesize

# Captured before any monkeypatching - app.tts.voicebox.httpx is the same
# module object as this file's httpx, so patching httpx.AsyncClient below
# would otherwise make the replacement call itself recursively.
_RealAsyncClient = httpx.AsyncClient

_SSE_BODY = (
    'data: {"id": "gen-1", "status": "generating", "duration": 0.0, "error": null}\n\n'
    'data: {"id": "gen-1", "status": "generating", "duration": 0.0, "error": null}\n\n'
    'data: {"id": "gen-1", "status": "completed", "duration": 3.4, "error": null}\n\n'
)


def _make_transport(*, sse_body: str = _SSE_BODY, audio_bytes: bytes = b"RIFF-fake-wav"):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/speak":
            return httpx.Response(200, json={"id": "gen-1", "status": "generating"})
        if request.url.path == "/generate/gen-1/status":
            return httpx.Response(200, text=sse_body, headers={"content-type": "text/event-stream"})
        if request.url.path == "/audio/gen-1":
            return httpx.Response(200, content=audio_bytes)
        raise AssertionError(f"unexpected request: {request.url}")

    return httpx.MockTransport(handler), calls


def test_synthesize_drives_the_full_speak_status_audio_sequence(monkeypatch):
    transport, calls = _make_transport()
    monkeypatch.setattr(
        "app.tts.voicebox.httpx.AsyncClient",
        lambda **kwargs: _RealAsyncClient(transport=transport, **kwargs),
    )

    result = asyncio.run(
        synthesize("привет джарвис", base_url="http://127.0.0.1:8000", profile="JarvisVoice")
    )

    assert result == b"RIFF-fake-wav"
    assert [c.url.path for c in calls] == ["/speak", "/generate/gen-1/status", "/audio/gen-1"]
    import json

    speak_body = json.loads(calls[0].content)
    assert speak_body == {
        "text": "привет джарвис",
        "engine": "chatterbox",
        "language": "ru",
        "profile": "JarvisVoice",
    }


def test_synthesize_raises_on_error_status(monkeypatch):
    error_sse = 'data: {"id": "gen-1", "status": "error", "duration": 0.0, "error": "model crashed"}\n\n'
    transport, _ = _make_transport(sse_body=error_sse)
    monkeypatch.setattr(
        "app.tts.voicebox.httpx.AsyncClient",
        lambda **kwargs: _RealAsyncClient(transport=transport, **kwargs),
    )

    with pytest.raises(RuntimeError, match="model crashed"):
        asyncio.run(synthesize("привет", base_url="http://127.0.0.1:8000", profile="JarvisVoice"))


def test_speak_plays_the_returned_audio_bytes(monkeypatch):
    transport, _ = _make_transport(audio_bytes=b"the-wav-bytes")
    monkeypatch.setattr(
        "app.tts.voicebox.httpx.AsyncClient",
        lambda **kwargs: _RealAsyncClient(transport=transport, **kwargs),
    )
    played = []
    monkeypatch.setattr("app.tts.voicebox.play_wav_bytes", lambda audio: played.append(audio))

    provider = VoiceboxTTSProvider(profile="JarvisVoice", base_url="http://127.0.0.1:8000")
    asyncio.run(provider.speak("привет"))

    assert played == [b"the-wav-bytes"]
