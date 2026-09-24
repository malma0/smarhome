"""No real Voicebox server is touched in these tests - httpx calls go
through a fake ASGI-less transport (httpx.MockTransport) that scripts the
exact call sequence the real local server is expected to follow: profile
lookup/creation (GET/POST /profiles, POST /profiles/{id}/samples), then
POST /speak, GET /generate/{id}/status, GET /audio/{id}."""

import asyncio
import json

import httpx
import pytest

from app.tts.voicebox import VoiceboxTTSProvider, ensure_profile, synthesize

# Captured before any monkeypatching - app.tts.voicebox.httpx is the same
# module object as this file's httpx, so patching httpx.AsyncClient below
# would otherwise make the replacement call itself recursively.
_RealAsyncClient = httpx.AsyncClient

_SSE_BODY = (
    'data: {"id": "gen-1", "status": "generating", "duration": 0.0, "error": null}\n\n'
    'data: {"id": "gen-1", "status": "generating", "duration": 0.0, "error": null}\n\n'
    'data: {"id": "gen-1", "status": "completed", "duration": 3.4, "error": null}\n\n'
)


def _make_transport(
    *,
    sse_body: str = _SSE_BODY,
    audio_bytes: bytes = b"RIFF-fake-wav",
    existing_profiles: tuple[str, ...] = ("JarvisVoice",),
):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path, method = request.url.path, request.method
        if path == "/profiles" and method == "GET":
            return httpx.Response(200, json=[{"id": f"id-{n}", "name": n} for n in existing_profiles])
        if path == "/profiles" and method == "POST":
            return httpx.Response(200, json={"id": "prof-new"})
        if path == "/profiles/prof-new/samples" and method == "POST":
            return httpx.Response(200, json={"id": "sample-1"})
        if path == "/speak":
            return httpx.Response(200, json={"id": "gen-1", "status": "generating"})
        if path == "/generate/gen-1/status":
            return httpx.Response(200, text=sse_body, headers={"content-type": "text/event-stream"})
        if path == "/audio/gen-1":
            return httpx.Response(200, content=audio_bytes)
        raise AssertionError(f"unexpected request: {method} {request.url}")

    return httpx.MockTransport(handler), calls


def _use_transport(monkeypatch, transport):
    monkeypatch.setattr(
        "app.tts.voicebox.httpx.AsyncClient",
        lambda **kwargs: _RealAsyncClient(transport=transport, **kwargs),
    )


def test_synthesize_drives_the_full_speak_status_audio_sequence(monkeypatch):
    transport, calls = _make_transport()
    _use_transport(monkeypatch, transport)

    result = asyncio.run(
        synthesize("привет джарвис", base_url="http://127.0.0.1:8000", profile="JarvisVoice")
    )

    assert result == b"RIFF-fake-wav"
    assert [c.url.path for c in calls] == ["/speak", "/generate/gen-1/status", "/audio/gen-1"]
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
    _use_transport(monkeypatch, transport)

    with pytest.raises(RuntimeError, match="model crashed"):
        asyncio.run(synthesize("привет", base_url="http://127.0.0.1:8000", profile="JarvisVoice"))


def test_speak_plays_the_returned_audio_bytes(monkeypatch):
    transport, _ = _make_transport(audio_bytes=b"the-wav-bytes")
    _use_transport(monkeypatch, transport)
    played = []
    monkeypatch.setattr("app.tts.voicebox.play_wav_bytes", lambda audio: played.append(audio))

    provider = VoiceboxTTSProvider(profile="JarvisVoice", base_url="http://127.0.0.1:8000")
    asyncio.run(provider.speak("привет"))

    assert played == [b"the-wav-bytes"]


# --- ensure_profile: the voice is defined by the WAV in voice_reference/,
# not only by whatever Voicebox's own database happens to contain ---


def test_ensure_profile_does_nothing_when_the_profile_already_exists(monkeypatch):
    transport, calls = _make_transport(existing_profiles=("ReyZi",))
    _use_transport(monkeypatch, transport)

    asyncio.run(ensure_profile(base_url="http://vb", profile="ReyZi", reference_wav=""))

    assert [(c.method, c.url.path) for c in calls] == [("GET", "/profiles")]


def test_ensure_profile_creates_a_missing_profile_from_the_reference_wav(monkeypatch, tmp_path):
    wav = tmp_path / "reyzi_sample.wav"
    wav.write_bytes(b"RIFF-sample")
    (tmp_path / "reyzi_sample.txt").write_text("  Ну и навык рисования.  ", encoding="utf-8")
    transport, calls = _make_transport(existing_profiles=("JarvisVoice",))
    _use_transport(monkeypatch, transport)

    asyncio.run(ensure_profile(base_url="http://vb", profile="ReyZi", reference_wav=str(wav)))

    assert [(c.method, c.url.path) for c in calls] == [
        ("GET", "/profiles"),
        ("POST", "/profiles"),
        ("POST", "/profiles/prof-new/samples"),
    ]
    assert json.loads(calls[1].content) == {
        "name": "ReyZi",
        "language": "ru",
        "voice_type": "cloned",
        "default_engine": "chatterbox",
    }
    upload = calls[2].content
    assert b"RIFF-sample" in upload
    assert "Ну и навык рисования.".encode("utf-8") in upload  # transcript sent, whitespace trimmed


def test_ensure_profile_errors_clearly_when_missing_and_no_reference_wav(monkeypatch):
    transport, _ = _make_transport(existing_profiles=())
    _use_transport(monkeypatch, transport)

    with pytest.raises(RuntimeError, match="VOICEBOX_REFERENCE_WAV"):
        asyncio.run(ensure_profile(base_url="http://vb", profile="ReyZi", reference_wav=""))


def test_ensure_profile_errors_when_the_transcript_file_is_missing(monkeypatch, tmp_path):
    wav = tmp_path / "reyzi_sample.wav"
    wav.write_bytes(b"RIFF-sample")  # no reyzi_sample.txt next to it
    transport, calls = _make_transport(existing_profiles=())
    _use_transport(monkeypatch, transport)

    with pytest.raises(RuntimeError, match="transcript"):
        asyncio.run(ensure_profile(base_url="http://vb", profile="ReyZi", reference_wav=str(wav)))

    assert [(c.method, c.url.path) for c in calls] == [("GET", "/profiles")]  # nothing half-created


def test_warm_up_generates_but_plays_nothing(monkeypatch):
    transport, calls = _make_transport()
    _use_transport(monkeypatch, transport)
    played = []
    monkeypatch.setattr("app.tts.voicebox.play_wav_bytes", lambda audio: played.append(audio))

    provider = VoiceboxTTSProvider(profile="JarvisVoice", base_url="http://127.0.0.1:8000")
    asyncio.run(provider.warm_up())

    assert "/speak" in [c.url.path for c in calls]
    assert played == []


def test_prepare_then_speak_checks_the_profile_once(monkeypatch):
    transport, calls = _make_transport()
    _use_transport(monkeypatch, transport)
    monkeypatch.setattr("app.tts.voicebox.play_wav_bytes", lambda audio: None)

    provider = VoiceboxTTSProvider(profile="JarvisVoice", base_url="http://127.0.0.1:8000")
    asyncio.run(provider.prepare())
    asyncio.run(provider.speak("раз"))

    assert sum(1 for c in calls if c.url.path == "/profiles") == 1


def test_provider_checks_the_profile_only_once_across_replies(monkeypatch):
    transport, calls = _make_transport()
    _use_transport(monkeypatch, transport)
    monkeypatch.setattr("app.tts.voicebox.play_wav_bytes", lambda audio: None)

    provider = VoiceboxTTSProvider(profile="JarvisVoice", base_url="http://127.0.0.1:8000")
    asyncio.run(provider.speak("раз"))
    asyncio.run(provider.speak("два"))

    assert sum(1 for c in calls if c.url.path == "/profiles") == 1
