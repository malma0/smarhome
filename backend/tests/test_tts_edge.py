import asyncio

import edge_tts

from app.tts.edge import synthesize


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
