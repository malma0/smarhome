"""Best quality/selection of the three TTS providers here - hundreds of
voices in ElevenLabs' Voice Library vs Piper's 4 or edge-tts's 2 for
Russian - but it's a real account with a real (if generous, no-card) free
tier: ~10 minutes of audio/month. Needs ELEVENLABS_API_KEY and
ELEVENLABS_VOICE_ID (picked from https://elevenlabs.io/app/voice-library).
"""

import asyncio

import httpx

from app.tts.edge import play_mp3_bytes  # mp3 playback is identical

DEFAULT_MODEL = "eleven_multilingual_v2"  # handles Russian well, unlike the English-only models
SYNTHESIS_TIMEOUT_SECONDS = 15


async def synthesize(text: str, voice_id: str, api_key: str, model: str = DEFAULT_MODEL) -> bytes:
    """Pure-ish and testable in isolation from playback: returns raw mp3
    bytes for the given text/voice, network call mocked in tests."""
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            headers={"xi-api-key": api_key, "Content-Type": "application/json"},
            json={"text": text, "model_id": model},
        )
    response.raise_for_status()
    return response.content


class ElevenLabsTTSProvider:
    def __init__(
        self,
        api_key: str,
        voice_id: str,
        model: str = DEFAULT_MODEL,
        monthly_char_budget: int = 9500,
    ):
        self._api_key = api_key
        self._voice_id = voice_id
        self._model = model
        # Soft, process-local guard against blowing through the free tier's
        # ~10k character/month allowance in one long dev session - it does
        # NOT persist across runs or actually track the real monthly reset,
        # it's just a visible nudge, not a hard quota system.
        self._budget = monthly_char_budget
        self._chars_used = 0

    async def speak(self, text: str) -> None:
        self._chars_used += len(text)
        if self._chars_used > self._budget:
            print(
                f"(похоже, за этот запуск озвучено ~{self._chars_used} символов - "
                f"это уже около бесплатного месячного лимита ElevenLabs)"
            )
        audio_bytes = await asyncio.wait_for(
            synthesize(text, self._voice_id, self._api_key, self._model),
            timeout=SYNTHESIS_TIMEOUT_SECONDS,
        )
        await asyncio.to_thread(play_mp3_bytes, audio_bytes)
