"""Desktop voice loop for Jarvis - phase 1 of docs/TZ.md: talk to Jarvis on
the computer itself, not through a browser tab and not the real production
voice pipeline (phase 9, LiveKit/Vapi/Retell + ElevenLabs) that will run in
an actual house. Both pieces here are free, no new accounts:

- STT: records the mic, sends the clip to Groq's free Whisper endpoint
  (reuses GROQ_API_KEY - independent of whichever LLM_PROVIDER answers the
  chat itself).
- TTS: pluggable via app.tts.base.TTSProvider - defaults to a fully
  open-source, offline voice (app.tts.piper), falls back to the OS's own
  SAPI voices (app.tts.sapi) if the configured provider fails to build or
  speak for any reason.

Usage: python voice_app.py
"""

import asyncio
import io
import time
import wave
from typing import Any

import httpx
import numpy as np
import sounddevice as sd

from app.agent import build_default_agent
from app.config import settings
from app.tts.base import TTSProvider

SAMPLE_RATE = 16000
WHISPER_MODEL = "whisper-large-v3-turbo"


def frames_to_wav_bytes(frames: list[np.ndarray], sample_rate: int = SAMPLE_RATE) -> bytes:
    """Pure and testable: turns recorded int16 mono chunks into a WAV file
    in memory. Returns b"" for an empty recording rather than raising."""
    if not frames:
        return b""
    audio = np.concatenate(frames, axis=0)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # int16
        wf.setframerate(sample_rate)
        wf.writeframes(audio.tobytes())
    return buffer.getvalue()


async def transcribe(wav_bytes: bytes, api_key: str, base_url: str) -> str:
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{base_url}/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": ("speech.wav", wav_bytes, "audio/wav")},
            data={"model": WHISPER_MODEL, "language": "ru"},
        )
    response.raise_for_status()
    return response.json()["text"].strip()


def record_until_enter() -> bytes:
    """Blocks on a second Enter press while a background PortAudio callback
    (driven by sounddevice's own thread) keeps appending mic chunks."""
    frames: list[np.ndarray] = []

    def callback(indata: np.ndarray, frame_count: int, time_info: Any, status: Any) -> None:
        frames.append(indata.copy())

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", callback=callback):
        input()  # second Enter press stops the recording

    return frames_to_wav_bytes(frames)


def build_tts_provider() -> TTSProvider:
    if settings.tts_provider == "elevenlabs":
        from app.tts.elevenlabs import ElevenLabsTTSProvider

        if not settings.elevenlabs_api_key or not settings.elevenlabs_voice_id:
            raise ValueError("ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID must both be set in .env")
        return ElevenLabsTTSProvider(
            api_key=settings.elevenlabs_api_key,
            voice_id=settings.elevenlabs_voice_id,
            model=settings.elevenlabs_model,
        )
    if settings.tts_provider == "piper":
        from app.tts.piper import PiperTTSProvider

        return PiperTTSProvider(settings.piper_model_path, config_path=settings.piper_config_path)
    if settings.tts_provider == "edge":
        from app.tts.edge import EdgeTTSProvider

        return EdgeTTSProvider(voice=settings.edge_tts_voice)
    if settings.tts_provider == "sapi":
        from app.tts.sapi import SapiTTSProvider

        return SapiTTSProvider()
    raise ValueError(f"Unsupported TTS_PROVIDER {settings.tts_provider!r}. Valid: elevenlabs, piper, edge, sapi")


async def speak(primary: TTSProvider, fallback: TTSProvider, text: str) -> None:
    """No timeout wraps the whole call on purpose: playback duration alone
    can legitimately exceed any fixed number for a long reply, and cutting
    it off mid-sentence just to start the fallback speaking the same text
    on top of it is worse than the problem (this actually happened - a
    15s ceiling here fired while a normal-length reply was still being
    played, so both voices spoke at once). Any timeout on the risky,
    genuinely-hangable part (a network TTS call) belongs inside that
    provider's own speak(), around just the synthesis step - see
    app/tts/edge.py."""
    started = time.monotonic()
    try:
        await primary.speak(text)
        print(f"   (озвучено через {settings.tts_provider} за {time.monotonic() - started:.1f}с)")
    except Exception as exc:  # noqa: BLE001 - synthesis failing shouldn't kill the loop
        print(f"(озвучка через {settings.tts_provider} не удалась: {exc} - пробую офлайн-голос)")
        await fallback.speak(text)


async def main() -> None:
    if not settings.groq_api_key:
        print("GROQ_API_KEY не задан в .env - он нужен для распознавания речи (Whisper), даже если LLM_PROVIDER не groq.")
        return

    agent = build_default_agent()
    from app.tts.sapi import SapiTTSProvider

    tts_fallback = SapiTTSProvider()
    try:
        tts_provider = build_tts_provider()
    except Exception as exc:  # noqa: BLE001 - e.g. piper model files not downloaded yet
        print(f"Не удалось запустить {settings.tts_provider}: {exc}\nИспользую офлайн-голос вместо него.")
        tts_provider = tts_fallback

    session_id = "voice-session"
    try:
        resident_id = input("resident id (enter for 'default')> ").strip() or "default"
    except (EOFError, KeyboardInterrupt):
        resident_id = "default"

    print(f"\nJarvis voice - resident '{resident_id}'. Ctrl+C для выхода.\n")

    while True:
        try:
            input("[Enter] чтобы начать говорить...")
        except (EOFError, KeyboardInterrupt):
            break

        print("🔴 Слушаю... нажмите Enter ещё раз, чтобы закончить")
        wav_bytes = record_until_enter()
        if not wav_bytes:
            print("(ничего не записано)\n")
            continue

        print("Распознаю...")
        try:
            text = await transcribe(wav_bytes, settings.groq_api_key, settings.groq_base_url)
        except Exception as exc:  # noqa: BLE001 - a failed request shouldn't kill the loop
            print(f"Ошибка распознавания: {exc}\n")
            continue

        if not text:
            print("(не удалось разобрать речь)\n")
            continue

        print(f"you> {text}")
        result = await agent.chat(session_id, resident_id, text)
        print(f"jarvis> {result['response']}")
        for action in result["actions"]:
            print(f"   [action] {action['tool']}({action['input']}) -> {action['result']}")
        print()

        await speak(tts_provider, tts_fallback, result["response"])


if __name__ == "__main__":
    asyncio.run(main())
