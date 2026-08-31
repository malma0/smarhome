"""Desktop voice loop for Jarvis - phase 1 of docs/TZ.md: talk to Jarvis on
the computer itself, not through a browser tab and not the real production
voice pipeline (phase 9, LiveKit/Vapi/Retell + ElevenLabs) that will run in
an actual house. Both pieces here are free, no new accounts:

- STT: records the mic, sends the clip to Groq's free Whisper endpoint
  (reuses GROQ_API_KEY - independent of whichever LLM_PROVIDER answers the
  chat itself).
- TTS: the OS's own SAPI voices via pyttsx3, fully offline.

Usage: python voice_app.py
"""

import asyncio
import io
import wave
from typing import Any

import httpx
import numpy as np
import pyttsx3
import sounddevice as sd

from app.agent import build_default_agent
from app.config import settings

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


def pick_voice(engine: "pyttsx3.Engine", language_hint: str = "ru") -> None:
    for voice in engine.getProperty("voices"):
        if language_hint in voice.id.lower() or any(language_hint in lang.lower() for lang in getattr(voice, "languages", [])):
            engine.setProperty("voice", voice.id)
            return
    # No Russian voice installed - fall back to whatever the default is.


async def main() -> None:
    if not settings.groq_api_key:
        print("GROQ_API_KEY не задан в .env - он нужен для распознавания речи (Whisper), даже если LLM_PROVIDER не groq.")
        return

    agent = build_default_agent()
    tts_engine = pyttsx3.init()
    pick_voice(tts_engine)

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

        tts_engine.say(result["response"])
        tts_engine.runAndWait()


if __name__ == "__main__":
    asyncio.run(main())
