import asyncio

import edge_tts
import pytest

from app.tts.edge import EdgeTTSProvider, synthesize


class _FakeCommunicate:
    def __init__(self, text: str, voice: str):
        self.text = text
        self.voice = voice

    async def stream(self):
        yield {"type": "audio", "data": b"chunk-one-"}
        yield {"type": "WordBoundary", "data": b"ignored-metadata"}
        yield {"type": "audio", "data": b"chunk-two"}


def test_synthesize_concatenates_only_audio_chunks(monkeypatch):
    monkeypatch.setattr(edge_tts, "Communicate", _FakeCommunicate)

    result = asyncio.run(synthesize("привет", voice="ru-RU-DmitryNeural"))

    assert result == b"chunk-one-chunk-two"


def test_synthesize_passes_text_and_voice_through(monkeypatch):
    captured = {}

    class _CapturingCommunicate(_FakeCommunicate):
        def __init__(self, text: str, voice: str):
            captured["text"] = text
            captured["voice"] = voice
            super().__init__(text, voice)

    monkeypatch.setattr(edge_tts, "Communicate", _CapturingCommunicate)

    asyncio.run(synthesize("привет джарвис", voice="ru-RU-SvetlanaNeural"))

    assert captured == {"text": "привет джарвис", "voice": "ru-RU-SvetlanaNeural"}


class _HangingCommunicate:
    def __init__(self, text: str, voice: str):
        pass

    async def stream(self):
        await asyncio.sleep(10)
        yield {"type": "audio", "data": b"too-late"}


def test_speak_times_out_on_a_hung_network_call_without_playing_anything(monkeypatch):
    """A hung synthesize() call must raise (so voice_app.speak() falls back
    to SAPI) rather than block forever - this is exactly the scenario the
    timeout exists for, unlike wrapping playback (see voice_app.py)."""
    monkeypatch.setattr(edge_tts, "Communicate", _HangingCommunicate)
    monkeypatch.setattr("app.tts.edge.SYNTHESIS_TIMEOUT_SECONDS", 0.05)
    played = []
    monkeypatch.setattr("app.tts.edge.play_mp3_bytes", lambda audio: played.append(audio))

    provider = EdgeTTSProvider(voice="ru-RU-DmitryNeural")

    with pytest.raises(TimeoutError):
        asyncio.run(provider.speak("привет"))

    assert played == []
