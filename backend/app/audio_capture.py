"""Microphone capture for voice_app.py that doesn't lose the start or end of
a phrase, plus a voice-activity check so silence never reaches Whisper.

Start of phrase: the original version opened the mic only after the user
pressed Enter. Opening an audio device takes a noticeable fraction of a
second, and people start talking the instant they press the key, so the
first word regularly got cut ("Включи на нём видео" came through as "На нём
видео"). Now the mic stays open for the whole session and the last
PREROLL_SECONDS of audio are always kept in a small rolling buffer (memory
only, overwritten continuously, never saved): pressing Enter turns that
buffer into the start of the recording instead of starting from silence.

End of phrase: recording continues for TAIL_SECONDS after the stop key,
since people tend to press it while still finishing the last word.

Silence: Whisper hallucinates on silent or near-silent clips - verified
live, faint room hiss came back as "Спасибо." or "Дисклеймер" - and Groq's
Whisper reports no_speech_prob=0 even for pure silence, so its own flag
can't catch it. contains_speech() runs WebRTC's voice activity detector
(webrtcvad, already installed for speaker ID) before anything is sent.
"""

import collections
import queue
import threading
import time
from collections.abc import Callable
from typing import Any, NamedTuple

import numpy as np

BLOCK_SECONDS = 0.05
PREROLL_SECONDS = 0.5
TAIL_SECONDS = 0.3

# Measured on real audio: mode 2 flagged ~0.1s of "speech" in 2s of room
# hiss, but ~8s in 10s of genuinely quiet (-50 dB) speech. Mode 3 missed
# that quiet speech almost entirely (0.7s of 10s), so it's too strict here.
VAD_MODE = 2
VAD_FRAME_SECONDS = 0.03
MIN_SPEECH_SECONDS = 0.25


class PrerollBuffer:
    """The buffering logic on its own, no audio device involved - fed chunks
    from the mic callback (sounddevice's own thread) and read from the main
    thread, hence the lock."""

    def __init__(self, preroll_chunks: int):
        self._preroll: collections.deque[np.ndarray] = collections.deque(maxlen=preroll_chunks)
        self._recording: list[np.ndarray] | None = None
        self._lock = threading.Lock()

    def feed(self, chunk: np.ndarray) -> None:
        with self._lock:
            if self._recording is not None:
                self._recording.append(chunk)
            else:
                self._preroll.append(chunk)

    def begin(self) -> None:
        with self._lock:
            self._recording = list(self._preroll)
            self._preroll.clear()

    def end(self) -> list[np.ndarray]:
        with self._lock:
            frames = self._recording or []
            self._recording = None
            return frames


class MicRecorder:
    """Keeps one input stream open for the whole session. Windows shows the
    "microphone in use" indicator the entire time the app runs - that's
    this, the pre-roll buffer, not a recording being saved."""

    def __init__(self, sample_rate: int, preroll_seconds: float = PREROLL_SECONDS):
        import sounddevice as sd

        self._buffer = PrerollBuffer(round(preroll_seconds / BLOCK_SECONDS))
        self._stream = sd.InputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="int16",
            blocksize=int(sample_rate * BLOCK_SECONDS),
            callback=self._callback,
        )
        self._stream.start()

    def _callback(self, indata: np.ndarray, frame_count: int, time_info: Any, status: Any) -> None:
        self._buffer.feed(indata.copy())

    def begin(self) -> None:
        self._buffer.begin()

    def end(self, tail_seconds: float = TAIL_SECONDS) -> list[np.ndarray]:
        time.sleep(tail_seconds)
        return self._buffer.end()

    def close(self) -> None:
        self._stream.stop()
        self._stream.close()


def speech_seconds(frames: list[np.ndarray], sample_rate: int) -> float:
    """How much of the recording webrtcvad considers speech. webrtcvad only
    accepts 8/16/32/48 kHz and 10/20/30 ms frames of int16 mono."""
    import webrtcvad

    if not frames:
        return 0.0
    audio = np.concatenate(frames, axis=0).reshape(-1).astype(np.int16)
    vad = webrtcvad.Vad(VAD_MODE)
    frame_len = int(sample_rate * VAD_FRAME_SECONDS)
    speech_frames = sum(
        vad.is_speech(audio[i : i + frame_len].tobytes(), sample_rate)
        for i in range(0, len(audio) - frame_len + 1, frame_len)
    )
    return speech_frames * VAD_FRAME_SECONDS


def contains_speech(frames: list[np.ndarray], sample_rate: int) -> bool:
    return speech_seconds(frames, sample_rate) >= MIN_SPEECH_SECONDS


# --- hands-free mode: continuous listening, cut into phrases by VAD ---


def make_vad(sample_rate: int) -> Callable[[np.ndarray], bool]:
    import webrtcvad

    vad = webrtcvad.Vad(VAD_MODE)
    return lambda frame: vad.is_speech(frame.reshape(-1).astype(np.int16).tobytes(), sample_rate)


class UtteranceSegmenter:
    """Turns a continuous stream of 30 ms frames into separate phrases, with
    no key presses. Decisions are by proportion over a sliding window, not
    by an unbroken run: a phrase starts when >= start_ratio of the last
    start_seconds is speech (so a cough or a door slam doesn't open one),
    and ends when >= end_ratio of the last end_silence_seconds is silence.

    The first version required unbroken silence to end a phrase, and a real
    test failed: the VAD flags a stray frame of room hiss as speech now and
    then, each one restarted the count, and a phrase followed by 2s of
    ordinary hiss never ended at all.

    The pre-roll puts the audio just before the detected start back in
    front, for the same first-word reason as push-to-talk. Pure logic -
    is_speech is injected."""

    def __init__(
        self,
        is_speech: Callable[[np.ndarray], bool],
        frame_seconds: float = VAD_FRAME_SECONDS,
        preroll_seconds: float = PREROLL_SECONDS,
        start_seconds: float = 0.3,
        start_ratio: float = 0.7,
        # 0.8 felt sluggish in real use ("I stopped - it should know right
        # away"); 0.6 still clears normal pauses between words and at commas.
        end_silence_seconds: float = 0.6,
        end_ratio: float = 0.9,
        max_seconds: float = 15.0,
    ):
        self._is_speech = is_speech
        self._preroll: collections.deque[np.ndarray] = collections.deque(
            maxlen=max(1, round(preroll_seconds / frame_seconds))
        )
        self._start_window: collections.deque[bool] = collections.deque(
            maxlen=max(1, round(start_seconds / frame_seconds))
        )
        self._end_window: collections.deque[bool] = collections.deque(
            maxlen=max(1, round(end_silence_seconds / frame_seconds))
        )
        self._start_ratio = start_ratio
        self._end_ratio = end_ratio
        self._max_frames = round(max_seconds / frame_seconds)
        self.reset()

    def reset(self) -> None:
        self._preroll.clear()
        self._start_window.clear()
        self._end_window.clear()
        self._frames: list[np.ndarray] | None = None

    @property
    def in_phrase(self) -> bool:
        return self._frames is not None

    @property
    def current_frames(self) -> list[np.ndarray]:
        """The phrase so far (pre-roll included) - empty between phrases."""
        return list(self._frames or [])

    @staticmethod
    def _full(window: collections.deque) -> bool:
        return len(window) == window.maxlen

    def feed(self, frame: np.ndarray) -> list[np.ndarray] | None:
        """Returns a finished phrase's frames, or None while still waiting."""
        speech = self._is_speech(frame)
        if self._frames is None:
            self._preroll.append(frame)
            self._start_window.append(speech)
            window = self._start_window
            if self._full(window) and sum(window) >= self._start_ratio * len(window):
                self._frames = list(self._preroll)
                self._preroll.clear()
                self._end_window.clear()
            return None

        self._frames.append(frame)
        self._end_window.append(speech)
        window = self._end_window
        silent = len(window) - sum(window)
        if (self._full(window) and silent >= self._end_ratio * len(window)) or len(self._frames) >= self._max_frames:
            phrase = self._frames
            self.reset()
            return phrase
        return None


def mic_level(frame: np.ndarray) -> float:
    """0..1 loudness for display: -60 dBFS (a quiet room) -> 0, -20 dBFS
    (speaking close to the mic) -> 1."""
    samples = frame.reshape(-1).astype(np.float64)
    if samples.size == 0:
        return 0.0
    rms = np.sqrt(np.mean(samples**2))
    if rms <= 0:
        return 0.0
    dbfs = 20 * np.log10(rms / 32768)
    return float(min(1.0, max(0.0, (dbfs + 60) / 40)))


MIC_LEVEL_INTERVAL_SECONDS = 0.08


class Phrase(NamedTuple):
    frames: list[np.ndarray]
    # What the streaming transcriber heard (the local wake-word check) -
    # None when no transcriber was given.
    text: str | None = None


def _pcm16(frame: np.ndarray) -> bytes:
    return frame.reshape(-1).astype(np.int16).tobytes()


class PhraseStreamer:
    """Cuts frames into phrases (UtteranceSegmenter) and, if given a
    transcriber factory, runs recognition on each phrase *while it's being
    spoken*: pre-roll and every following frame go in as they arrive, so the
    text is ready as soon as the phrase ends. Pure logic - no audio device."""

    def __init__(self, segmenter: UtteranceSegmenter, transcriber_factory: Callable[[], Any] | None = None):
        self._segmenter = segmenter
        self._factory = transcriber_factory
        self._transcript = None

    def reset(self) -> None:
        self._segmenter.reset()
        self._transcript = None

    def feed(self, frame: np.ndarray) -> Phrase | None:
        was_in_phrase = self._segmenter.in_phrase
        frames = self._segmenter.feed(frame)
        if self._factory is not None:
            if not was_in_phrase and self._segmenter.in_phrase:
                self._transcript = self._factory()
                for earlier in self._segmenter.current_frames:  # pre-roll + this frame
                    self._transcript.feed(_pcm16(earlier))
            elif was_in_phrase and self._transcript is not None:
                self._transcript.feed(_pcm16(frame))
        if frames is None:
            return None
        text = self._transcript.finish() if self._transcript is not None else None
        self._transcript = None
        return Phrase(frames, text)


class HandsFreeListener:
    """Mic -> 30 ms frames -> PhraseStreamer on its own thread, so audio
    keeps being captured, cut into phrases and (optionally) transcribed
    while the main loop is busy. mute() while Jarvis is working/speaking -
    otherwise it would hear its own voice as the next phrase.

    on_level, if given, gets the mic loudness (0..1) about 12 times a
    second while unmuted - for a front end's listening animation.
    transcriber_factory, if given, makes one streaming transcriber per
    phrase (WakeWordDetector.stream) - see PhraseStreamer."""

    def __init__(
        self,
        sample_rate: int,
        on_level: Callable[[float], None] | None = None,
        transcriber_factory: Callable[[], Any] | None = None,
    ):
        import sounddevice as sd

        frame_len = int(sample_rate * VAD_FRAME_SECONDS)
        self._on_level = on_level
        self._last_level_at = 0.0
        self._frames: queue.Queue[np.ndarray] = queue.Queue(maxsize=500)
        self._phrases: queue.Queue[Phrase] = queue.Queue()
        self._streamer = PhraseStreamer(UtteranceSegmenter(make_vad(sample_rate)), transcriber_factory)
        self._muted = threading.Event()
        self._stop = threading.Event()
        self._stream = sd.InputStream(
            samplerate=sample_rate, channels=1, dtype="int16", blocksize=frame_len, callback=self._callback
        )
        self._stream.start()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _callback(self, indata: np.ndarray, frame_count: int, time_info: Any, status: Any) -> None:
        try:
            self._frames.put_nowait(indata.copy())
        except queue.Full:  # main side stalled - dropping audio beats unbounded memory
            pass

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                frame = self._frames.get(timeout=0.2)
            except queue.Empty:
                continue
            if self._muted.is_set():
                self._streamer.reset()
                continue
            if self._on_level is not None:
                now = time.monotonic()
                if now - self._last_level_at >= MIC_LEVEL_INTERVAL_SECONDS:
                    self._last_level_at = now
                    try:
                        self._on_level(mic_level(frame))
                    except Exception:  # noqa: BLE001 - a UI hiccup must never stop listening
                        pass
            try:
                phrase = self._streamer.feed(frame)
            except Exception:  # noqa: BLE001 - a transcriber hiccup must not kill listening
                self._streamer.reset()
                continue
            if phrase is not None:
                self._phrases.put(phrase)

    def mute(self) -> None:
        self._muted.set()
        _drain(self._phrases)

    def unmute(self) -> None:
        _drain(self._frames)
        self._muted.clear()

    def next_phrase(self, timeout: float | None = None) -> Phrase | None:
        try:
            return self._phrases.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self._stop.set()
        self._stream.stop()
        self._stream.close()


def _drain(q: queue.Queue) -> None:
    while True:
        try:
            q.get_nowait()
        except queue.Empty:
            return
