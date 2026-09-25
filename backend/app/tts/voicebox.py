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
from pathlib import Path

import httpx

from app.http_client import ssl_context

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
    async with httpx.AsyncClient(timeout=GENERATION_TIMEOUT_SECONDS, verify=ssl_context()) as client:
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

    from app.tts import playback

    playback.announce(audio, rate)
    sd.play(audio, samplerate=rate)
    sd.wait()


async def ensure_profile(
    *,
    base_url: str,
    profile: str,
    reference_wav: str,
    engine: str = "chatterbox",
    language: str = "ru",
) -> None:
    """Creates the voice profile from reference_wav if the running Voicebox
    doesn't have one by that name yet. The voice is defined by the WAV in
    this repo's voice_reference/ folder, not only by whatever happens to be
    in Voicebox's own database - so a fresh Voicebox install, or a different
    data folder (Voicebox launched from inside the Claude desktop app gets a
    sandboxed copy of its app-data, not the user's real one), still ends up
    speaking with the right voice.

    Voicebox requires a transcript of every sample: it's read from a .txt
    file next to the WAV with the same name (reyzi_sample.wav ->
    reyzi_sample.txt)."""
    async with httpx.AsyncClient(timeout=30, verify=ssl_context()) as client:
        response = await client.get(f"{base_url}/profiles")
        response.raise_for_status()
        if any(p.get("name") == profile for p in response.json()):
            return

        if not reference_wav:
            raise RuntimeError(
                f"Voicebox has no profile named {profile!r}, and VOICEBOX_REFERENCE_WAV isn't set to create it from"
            )
        wav_path = Path(reference_wav)
        text_path = wav_path.with_suffix(".txt")
        if not wav_path.exists() or not text_path.exists():
            raise RuntimeError(
                f"Can't create Voicebox profile {profile!r}: need both {wav_path} and its transcript {text_path}"
            )

        response = await client.post(
            f"{base_url}/profiles",
            json={"name": profile, "language": language, "voice_type": "cloned", "default_engine": engine},
        )
        response.raise_for_status()
        profile_id = response.json()["id"]

        response = await client.post(
            f"{base_url}/profiles/{profile_id}/samples",
            files={"file": (wav_path.name, wav_path.read_bytes(), "audio/wav")},
            data={"reference_text": text_path.read_text(encoding="utf-8").strip()},
            timeout=120,
        )
        response.raise_for_status()


def _postprocess(
    audio_bytes: bytes, cleanup: bool, tempo: float, ending_tempo: float = None, ending_share: float = 0.0
) -> bytes:
    """app.tts.cleanup and app.tts.tempo, ~0.1 s each per reply."""
    from app.tts.cleanup import clean_speech, transform_wav_bytes
    from app.tts.tempo import change_tempo_with_ending

    def transform(samples, rate):
        if cleanup:
            samples = clean_speech(samples, rate)
        return change_tempo_with_ending(samples, rate, tempo, ending_tempo or tempo, ending_share)

    try:
        return transform_wav_bytes(audio_bytes, transform)
    except Exception as exc:  # noqa: BLE001 - unprocessed speech beats no speech
        print(f"(обработка голоса не сработала: {exc})")
        return audio_bytes


class VoiceboxTTSProvider:
    announces_playback = True  # see app.tts.playback

    def __init__(
        self,
        *,
        profile: str,
        base_url: str = DEFAULT_BASE_URL,
        engine: str = "chatterbox",
        language: str = "ru",
        reference_wav: str = "",
        stresser=None,
        cleanup: bool = False,
        tempo: float = 1.0,
        ending_tempo: float | None = None,
    ):
        self._profile = profile
        self._base_url = base_url
        self._engine = engine
        self._language = language
        self._reference_wav = reference_wav
        self._profile_ready = False
        # app.tts.stress.RussianStresser (or anything with load()/stress()):
        # adds the stress marks Chatterbox was trained on but Voicebox's
        # bundle never adds - see that module for the full story.
        self._stresser = stresser if language == "ru" else None
        # app.tts.cleanup: hum, hiss and breaths the clone copied from its
        # reference clip, removed before playback (~0.1 s per reply).
        self._cleanup = cleanup
        # app.tts.tempo: >1 speeds replies up without raising the pitch.
        self._tempo = tempo
        # The reply's last sentence at its own speed (None: same as tempo).
        self._ending_tempo = ending_tempo or tempo

    async def _prepare_text(self, text: str) -> str:
        if self._stresser is None:
            return text
        try:
            return await asyncio.to_thread(self._stresser.stress, text)
        except Exception as exc:  # noqa: BLE001 - unstressed speech beats no speech
            print(f"(ударения отключены: {exc})")
            self._stresser = None
            return text

    async def prepare(self) -> None:
        """Makes sure the voice profile exists - one quick GET, plus a
        one-time upload if it's missing. Done once per provider."""
        if not self._profile_ready:
            await ensure_profile(
                base_url=self._base_url,
                profile=self._profile,
                reference_wav=self._reference_wav,
                engine=self._engine,
                language=self._language,
            )
            self._profile_ready = True

    async def warm_up(self) -> None:
        """Generates a throwaway phrase so Voicebox loads its ~3 GB model
        before the first real reply needs it (measured: a cold "Привет."
        took 39s, a warm "Включаю свет." 25s). Also loads the stress model
        (~16s from disk). voice_app runs this in the background at startup;
        the audio is discarded."""
        await self._prepare_text("Привет.")
        await synthesize(
            "Привет.",
            base_url=self._base_url,
            profile=self._profile,
            engine=self._engine,
            language=self._language,
        )

    async def speak(self, text: str) -> None:
        await self.prepare()
        audio_bytes = await synthesize(
            await self._prepare_text(text),
            base_url=self._base_url,
            profile=self._profile,
            engine=self._engine,
            language=self._language,
        )
        if self._cleanup or self._tempo != 1.0 or self._ending_tempo != 1.0:
            from app.tts.tempo import last_sentence_share

            audio_bytes = await asyncio.to_thread(
                _postprocess, audio_bytes, self._cleanup, self._tempo, self._ending_tempo, last_sentence_share(text)
            )
        await asyncio.to_thread(play_wav_bytes, audio_bytes)
