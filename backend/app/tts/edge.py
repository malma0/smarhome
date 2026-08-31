"""Free neural TTS via Microsoft Edge's read-aloud service (edge-tts package)
- no API key, no account, noticeably more natural than legacy SAPI voices.
Needs internet; it's a reverse-engineered API (not an official Microsoft
product for this use), so voice_app.py falls back to SapiTTSProvider if a
call fails rather than treating that as fatal.
"""

import asyncio
import os
import tempfile


async def synthesize(text: str, voice: str) -> bytes:
    """Pure-ish and testable in isolation from playback: returns raw mp3
    bytes for the given text/voice, network call mocked in tests."""
    import edge_tts

    communicate = edge_tts.Communicate(text, voice=voice)
    chunks = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            chunks.append(chunk["data"])
    return b"".join(chunks)


def play_mp3_bytes(audio_bytes: bytes) -> None:
    import pygame

    if not pygame.mixer.get_init():
        pygame.mixer.init()

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(audio_bytes)
        path = f.name
    try:
        pygame.mixer.music.load(path)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            pygame.time.wait(100)
    finally:
        pygame.mixer.music.unload()
        os.unlink(path)


class EdgeTTSProvider:
    def __init__(self, voice: str = "ru-RU-DmitryNeural"):
        self._voice = voice

    async def speak(self, text: str) -> None:
        audio_bytes = await synthesize(text, self._voice)
        await asyncio.to_thread(play_mp3_bytes, audio_bytes)
