"""Fully open-source, fully offline neural TTS via Piper
(github.com/rhasspy/piper) - unlike edge-tts (free, but still a Microsoft
service) these are downloadable model weights (see backend/voices/) that run
entirely locally on CPU: no network call, no vendor at all once the model
file is on disk. Verified on this machine: ~4s one-time model load, under
1s per short reply after that - fast enough for real conversation.
"""

import asyncio
from typing import Any

import numpy as np


def chunks_to_audio_array(chunks: list[Any]) -> np.ndarray:
    """Pure and testable: concatenates Piper's AudioChunk objects (anything
    exposing .audio_float_array) into one array ready for playback."""
    arrays = [chunk.audio_float_array for chunk in chunks]
    if not arrays:
        return np.array([], dtype=np.float32)
    return np.concatenate(arrays)


class PiperTTSProvider:
    def __init__(self, model_path: str, config_path: str | None = None):
        from piper import PiperVoice

        self._voice = PiperVoice.load(model_path, config_path=config_path)

    async def speak(self, text: str) -> None:
        await asyncio.to_thread(self._speak_sync, text)

    def _speak_sync(self, text: str) -> None:
        import sounddevice as sd

        audio = chunks_to_audio_array(list(self._voice.synthesize(text)))
        if audio.size == 0:
            return
        sd.play(audio, samplerate=self._voice.config.sample_rate)
        sd.wait()
