"""Local voice cloning via Voicebox (github.com/jamiepine/voicebox) running
the Chatterbox Multilingual model - the one provider here that isn't a
freemium vendor API: generation happens entirely on this machine, using a
resident's own cloned voice built from a short reference sample (see
backend/voice_reference/ and README for how that profile was created).

Needs voicebox-server.exe already running locally (default
http://127.0.0.1:8000) - launched from Voicebox's own install directory
(NOT this repo) so its data files don't land inside the repo. That default
port also happens to be this repo's own FastAPI port (JARVIS_PORT) - don't
run both on the same port.

CPU-only generation is genuinely slow (multiple seconds per short reply,
worse for long ones - see docs/TZ.md for measured numbers) - this is a
quality-over-speed trade, not a fast path. voice_app.py falls back to SAPI
if the server isn't reachable or a call fails, same as every other provider
here.
"""

import asyncio
import json

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
# CPU inference is slow (order of tens of seconds for a long reply on this
# machine's hardware) - this wraps the whole generate-and-fetch round trip,
# not just a handshake, so it needs real room rather than edge.py's 15s.
GENERATION_TIMEOUT_SECONDS = 120


async def synthesize(
    text: str,
    *,
    base_url: str,
    profile: str,
    engine: str = "chatterbox",
    language: str = "ru",
) -> bytes:
    """Pure-ish and testable in isolation from playback: drives the local
    Voicebox REST API end to end and returns raw WAV bytes. Three calls:
    POST /speak kicks off generation, GET /generate/{id}/status is an
    SSE-style endpoint that blocks/streams lines until the job finishes
    (the server appears to be busy with the CPU-bound generation itself
    meanwhile), then GET /audio/{id} fetches the finished WAV."""
    async with httpx.AsyncClient(timeout=GENERATION_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{base_url}/speak",
            json={"text": text, "engine": engine, "language": language, "profile": profile},
        )
        response.raise_for_status()
        generation_id = response.json()["id"]

        status = "generating"
        async with client.stream("GET", f"{base_url}/generate/{generation_id}/status") as stream:
            async for line in stream.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = json.loads(line[len("data:") :].strip())
                status = payload["status"]
                if status == "completed":
                    break
                if status == "error":
                    raise RuntimeError(f"Voicebox generation failed: {payload.get('error')}")

        if status != "completed":
            raise RuntimeError(f"Voicebox generation ended in unexpected status: {status!r}")

        audio_response = await client.get(f"{base_url}/audio/{generation_id}")
        audio_response.raise_for_status()
        return audio_response.content


def play_wav_bytes(audio_bytes: bytes) -> None:
    import io
    import wave

    import numpy as np
    import sounddevice as sd

    with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
        frames = wf.readframes(wf.getnframes())
        sample_width = wf.getsampwidth()
        channels = wf.getnchannels()
        rate = wf.getframerate()

    dtype = {1: np.uint8, 2: np.int16, 4: np.int32}[sample_width]
    audio = np.frombuffer(frames, dtype=dtype)
    if channels > 1:
        audio = audio.reshape(-1, channels)

    sd.play(audio, samplerate=rate)
    sd.wait()


class VoiceboxTTSProvider:
    def __init__(
        self,
        *,
        profile: str,
        base_url: str = DEFAULT_BASE_URL,
        engine: str = "chatterbox",
        language: str = "ru",
    ):
        self._profile = profile
        self._base_url = base_url
        self._engine = engine
        self._language = language

    async def speak(self, text: str) -> None:
        audio_bytes = await synthesize(
            text,
            base_url=self._base_url,
            profile=self._profile,
            engine=self._engine,
            language=self._language,
        )
        await asyncio.to_thread(play_wav_bytes, audio_bytes)
