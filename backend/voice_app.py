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

Two ways to talk (VOICE_MODE):
- "wake" (default, hands-free): no keys - just say the name. Until it's
  heard, phrases are checked locally (app.wake_word, Vosk) and dropped -
  nothing goes online or gets saved. See app.hands_free for the awake/asleep
  rules (a short listening window after the bare name, a follow-up window
  after each reply).
- "push": Enter to start, Enter to stop - also the automatic fallback if
  Vosk or its model isn't installed.

Who's talking is identified per-utterance from the voice itself
(app.speaker_id), on top of the manually-typed session default - see
identify_or_enroll_speaker(). Set VOICE_ID_ENABLED=false in .env to fall
back to the old always-ask-once-by-name behavior entirely.

Recognition quality, without training anything:
- the mic stays open with a short pre-roll so the first word isn't lost,
  and silent clips never reach Whisper (app.audio_capture);
- Whisper gets a vocabulary hint with this household's words
  (build_whisper_prompt).

Every utterance is kept as training data - audio, transcript, and a typed
correction when Whisper got it wrong (app.dataset).

Usage: python voice_app.py
"""

import asyncio
import io
import queue
import threading
import time
import wave
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from app import speaker_id
from app.agent import build_default_agent
from app.audio_capture import HandsFreeListener, MicRecorder, contains_speech, speech_seconds
from app.config import settings
from app.dataset import UtteranceLog
from app.hands_free import CUE, IGNORE, HandsFreeState, is_stop_phrase, name_heard
from app.reminders import ReminderStore, local_now
from app.http_client import shared_client
from app.memory import MemoryStore
from app.tts import playback
from app.tts.base import TTSProvider
from app.transcript_filter import is_hallucination
from app.tts.text import text_for_speech
from app.voice_ui import LISTENING, MUTED, SLEEPING, SPEAKING, THINKING, ConsoleUI, VoiceUI
from app.wake_word import WakeWordDetector, classify, parse_wake_words

SAMPLE_RATE = 16000
WHISPER_MODEL = "whisper-large-v3-turbo"

# Words Whisper otherwise tends to mishear - names, brands, app names.
# Extended from WHISPER_VOCABULARY in .env and with every known resident's
# name (see build_whisper_prompt).
DEFAULT_WHISPER_VOCABULARY = (
    "Джарвис",
    "YouTube",
    "Майнкрафт",
    "Steam",
    "Discord",
    "LibreWolf",
    "блокнот",
    "калькулятор",
    "браузер",
    "проводник",
)
# Whisper only reads the last 224 tokens of a prompt, and its tokenizer
# spends roughly one token per 2-3 Cyrillic characters - so the prompt is
# capped well below that rather than silently losing its start.
MAX_WHISPER_PROMPT_CHARS = 300


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


def build_whisper_prompt(extra_vocabulary: str, resident_ids: list[str]) -> str:
    """Comma-separated vocabulary hint: defaults, then WHISPER_VOCABULARY
    from .env, then resident names - deduplicated case-insensitively, and
    cut at a whole word once MAX_WHISPER_PROMPT_CHARS would be exceeded."""
    words = list(DEFAULT_WHISPER_VOCABULARY)
    words += [w.strip() for w in extra_vocabulary.split(",") if w.strip()]
    words += [r for r in resident_ids if r != "default"]

    seen: set[str] = set()
    prompt = ""
    for word in words:
        if word.lower() in seen:
            continue
        seen.add(word.lower())
        candidate = f"{prompt}, {word}" if prompt else word
        if len(candidate) > MAX_WHISPER_PROMPT_CHARS:
            break
        prompt = candidate
    return prompt


async def transcribe(wav_bytes: bytes, api_key: str, base_url: str, prompt: str = "") -> str:
    """Reuses one connection (app.http_client) - a fresh client per phrase
    measured 2.0-2.4 s for this call, a reused one 0.23-0.31 s."""
    data = {"model": WHISPER_MODEL, "language": "ru"}
    if prompt:
        data["prompt"] = prompt
    response = await shared_client().post(
        f"{base_url}/audio/transcriptions",
        headers={"Authorization": f"Bearer {api_key}"},
        files={"file": ("speech.wav", wav_bytes, "audio/wav")},
        data=data,
    )
    response.raise_for_status()
    return response.json()["text"].strip()


def record_until_enter(recorder: MicRecorder) -> list[np.ndarray]:
    """The first Enter press already happened (that's what called this) -
    begin() turns the pre-roll into the start of the recording, then this
    blocks until the second Enter press."""
    recorder.begin()
    input()  # second Enter press stops the recording
    return recorder.end()


# Measured on real recordings from this laptop's mic: a phrase with 4 s of
# speech matched its speaker's profile at 0.73-0.78, but short commands
# (~1 s of speech) scored 0.54-0.60 against the same person - no threshold
# lets those through while keeping other voices (up to 0.56) out. Too little
# voice to tell who it is.
MIN_SPEECH_FOR_VOICE_ID = 1.5

# A timer or reminder chimes again every few seconds until "стоп" - or gives
# up after a minute (it stays in the chat).
RING_EVERY_SECONDS = 6
RING_SECONDS = 60


def _existing_resident(memory: MemoryStore, typed: str) -> str:
    """'матвей' or ' Матвей ' -> the existing 'Матвей', not a second person."""
    name = " ".join(typed.split())
    for resident_id in memory.list_resident_ids():
        if resident_id.casefold() == name.casefold():
            return resident_id
    return name


def _learn(memory: MemoryStore, resident_id: str, embedding, wav_bytes: bytes) -> Path | None:
    """Adds a sample to the profile and keeps its audio (see
    speaker_id.save_sample_audio - the data for rebuilding profiles with a
    better model and for training a household-specific one). Returns where
    the audio went, or None if saving it failed."""
    speaker_id.enroll_resident(memory, resident_id, embedding)
    try:
        return speaker_id.save_sample_audio(resident_id, wav_bytes)
    except OSError:
        return None  # the profile itself is updated; losing one training clip isn't worth failing over


# How a phrase's speaker was decided - stored with it in the dataset, since
# the same phrases are training data for telling voices apart and a wrong
# label would teach the wrong thing.
CONFIDENT = "confident"  # cleared the model's threshold
CLOSER = "closer"  # clearly nearer one enrolled voice than the rest (see speaker_id.decide)
LAST = "last"  # nothing decisive on a short phrase: assumed whoever spoke last
ASKED = "asked"  # the person said who they are
UNKNOWN = "unknown"  # not recognized and not named, or voice ID unavailable
CORRECTED = "corrected"  # fixed by hand afterwards ("Кто говорил?")
SURE = {CONFIDENT, ASKED, CORRECTED}


@dataclass
class SpeakerDecision:
    resident: str
    how: str
    score: float | None = None
    voiceprint: Path | None = None  # set when this phrase became a profile sample


def identify_or_enroll_speaker(*args, **kwargs) -> str:
    """identify_speaker, returning just who it was."""
    return identify_speaker(*args, **kwargs).resident


def identify_speaker(
    memory: MemoryStore,
    wav_bytes: bytes,
    default_resident_id: str,
    threshold: float | None,
    prompt_for_name=None,
    ui: VoiceUI | None = None,
    speech_seconds: float | None = None,
    embedding=None,
) -> SpeakerDecision:
    """Voice-based resident ID (app.speaker_id) layered on top of the
    manually-typed session default: tries to recognize the speaker from
    this recording alone, and if nobody enrolled matches, offers to enroll
    a new voice under a name typed once. Falls back to default_resident_id
    whenever speaker ID can't help - unavailable/failed embedding, or a
    stranger who declines to give a name - so this only ever improves on
    the default, never blocks the conversation.

    Every confident match also feeds this recording back into that
    resident's profile (speaker_id.enroll_resident keeps only the most
    recent few, see MAX_ENROLLED_SAMPLES) - the profile quietly gets more
    robust from ordinary use instead of staying frozen at whatever the
    first, one-shot enrollment happened to sound like.

    Short phrases (under MIN_SPEECH_FOR_VOICE_ID seconds of speech) can
    still match, but never teach the profile (a noisy sample) and never
    trigger "who are you?" - without a match, whoever spoke last is assumed
    to still be speaking, instead of interrupting a quick command.

    Answering the question with an existing name (any case/spacing) adds
    this recording to that person's profile - that's how an existing voice
    that wasn't recognized gets better, not a second person."""
    ui = ui or ConsoleUI()
    short = speech_seconds is not None and speech_seconds < MIN_SPEECH_FOR_VOICE_ID
    if embedding is None:
        try:
            embedding = speaker_id.embed_wav_bytes(wav_bytes)
        except Exception as exc:  # noqa: BLE001 - e.g. the model isn't installed
            ui.info(f"(распознавание голоса недоступно: {exc})")
            return SpeakerDecision(default_resident_id, UNKNOWN)

    enrolled = speaker_id.load_enrolled_voiceprints(memory)
    match, confident, score = speaker_id.match_resident_scored(embedding, enrolled, threshold=threshold)
    if match:
        # Only a confident match on enough speech teaches the profile - a
        # short clip, or a "clearly closer than the other person" call, is
        # good enough to know who's talking but would be a noisy sample.
        voiceprint = _learn(memory, match, embedding, wav_bytes) if confident and not short else None
        ui.resident(match)
        return SpeakerDecision(match, CONFIDENT if confident else CLOSER, score, voiceprint)
    if short:
        return SpeakerDecision(default_resident_id, LAST if default_resident_id != "default" else UNKNOWN, score)

    prompt = "Не узнал голос — как вас зовут? (Enter, чтобы не запоминать) "
    known = sorted(enrolled)
    try:
        answer = prompt_for_name(prompt) if prompt_for_name else ui.ask_name(prompt, known)
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if not answer.strip():
        return SpeakerDecision(default_resident_id, UNKNOWN, score)

    name = _existing_resident(memory, answer)
    voiceprint = _learn(memory, name, embedding, wav_bytes)
    report_voices(memory, ui)
    if name in enrolled:
        ui.info(f"Добавил эту запись в голос «{name}» - дальше буду узнавать увереннее.")
    else:
        ui.info(f"Запомнил ваш голос как «{name}».")
    ui.resident(name)
    return SpeakerDecision(name, ASKED, score, voiceprint)


def reassign_speaker(session: "VoiceSession", utterance_id: str, name: str) -> bool:
    """'Кто говорил?' - fixes a phrase's speaker label in the dataset. If the
    phrase had become a sample in the wrong person's profile, its audio
    moves to the right person and both profiles are rebuilt from their
    saved audio; if it hadn't but has enough speech, it becomes a sample of
    the right person now. Whoever it really was is also the current
    speaker from here on."""
    if session.utterances is None:
        return False
    record = session.utterances.get(utterance_id)
    if record is None:
        return False
    memory, ui = session.agent.memory, session.ui
    name = _existing_resident(memory, name)
    previous = record.get("resident_id")
    audio = session.utterances.audio(record)
    fields = {"resident_id": name, "speaker_how": CORRECTED, "speaker_corrected_from": previous}

    old_print = record.get("voiceprint")
    if old_print and Path(old_print).exists() and previous != name:
        fields["voiceprint"] = str(speaker_id.move_sample_audio(Path(old_print), name))
        speaker_id.rebuild_profiles(memory, only={previous, name})
    elif not old_print and _wav_speech_seconds(audio) >= MIN_SPEECH_FOR_VOICE_ID:
        saved = _learn(memory, name, speaker_id.embed_wav_bytes(audio), audio)
        fields["voiceprint"] = str(saved) if saved else None

    session.utterances.update(utterance_id, **fields)
    session.resident_id = name
    ui.resident(name)
    report_voices(memory, ui)
    ui.info(f"Исправлено: голос - «{name}».")
    return True


def _wav_speech_seconds(wav_bytes: bytes) -> float:
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        samples = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
        rate = wf.getframerate()
    return speech_seconds([samples.reshape(-1, 1)], rate)


# Deliberate voice enrollment ("Записать голос" in the window): the person
# reads a few sentences aloud and each ~4 s piece with enough speech
# becomes a profile sample - long, clean samples, unlike the short commands
# a profile otherwise picks up in passing (which score poorly, see
# MIN_SPEECH_FOR_VOICE_ID).
ENROLL_SAMPLES_NEEDED = 3
ENROLL_TIMEOUT_SECONDS = 45.0
ENROLL_CHUNK_SECONDS = 4.0
ENROLL_MIN_SPEECH_SECONDS = 2.0


def enrollment_chunks(frames: list[np.ndarray]) -> list[list[np.ndarray]]:
    """Splits one phrase into ~ENROLL_CHUNK_SECONDS pieces (a short phrase
    stays whole; the last piece absorbs the remainder) and keeps those with
    at least ENROLL_MIN_SPEECH_SECONDS of actual speech."""
    audio = np.concatenate(frames, axis=0).reshape(-1, 1)
    chunk = int(ENROLL_CHUNK_SECONDS * SAMPLE_RATE)
    count = max(1, len(audio) // chunk) if len(audio) >= 1.5 * chunk else 1
    pieces = [audio[i * chunk : (i + 1) * chunk] for i in range(count - 1)] + [audio[(count - 1) * chunk :]]
    return [[piece] for piece in pieces if speech_seconds([piece], SAMPLE_RATE) >= ENROLL_MIN_SPEECH_SECONDS]


def enroll_from_phrase(memory: MemoryStore, name: str, frames: list[np.ndarray]) -> int:
    """Adds this phrase to name's voice profile; returns how many samples."""
    added = 0
    for piece in enrollment_chunks(frames):
        wav_bytes = frames_to_wav_bytes(piece)
        _learn(memory, name, speaker_id.embed_wav_bytes(wav_bytes), wav_bytes)
        added += 1
    return added


def report_voices(memory: MemoryStore, ui: VoiceUI) -> None:
    enrolled = speaker_id.load_enrolled_voiceprints(memory)
    ui.voices([{"name": name, "samples": len(samples)} for name, samples in sorted(enrolled.items())])


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
    if settings.tts_provider == "voicebox":
        from app.tts.voicebox import VoiceboxTTSProvider

        if not settings.voicebox_profile:
            raise ValueError("VOICEBOX_PROFILE must be set in .env (the cloned-voice profile name)")
        stresser = None
        if settings.voicebox_russian_stress:
            from app.tts.stress import RussianStresser

            stresser = RussianStresser(settings.ruaccent_model, workdir="models/ruaccent")
        return VoiceboxTTSProvider(
            stresser=stresser,
            profile=settings.voicebox_profile,
            base_url=settings.voicebox_base_url,
            engine=settings.voicebox_engine,
            language=settings.voicebox_language,
            reference_wav=settings.voicebox_reference_wav,
            cleanup=settings.voicebox_cleanup,
            tempo=settings.voicebox_tempo,
            ending_tempo=settings.voicebox_ending_tempo,
        )
    raise ValueError(
        f"Unsupported TTS_PROVIDER {settings.tts_provider!r}. Valid: elevenlabs, piper, edge, sapi, voicebox"
    )


async def warm_up_in_background(provider: TTSProvider, ui: VoiceUI | None = None) -> None:
    """For providers with a slow cold start (Voicebox loads a ~3 GB model
    on its first generation): prepare() runs here - it's quick, and doing it
    before the thread starts means nothing can race to create the voice
    profile twice - then warm_up() runs on its own thread and event loop.
    A background asyncio task wouldn't work: the main loop blocks in input()
    between turns, so the task would never get to run."""
    ui = ui or ConsoleUI()
    prepare = getattr(provider, "prepare", None)
    warm_up = getattr(provider, "warm_up", None)
    if prepare is None or warm_up is None:
        return
    try:
        await prepare()
    except Exception as exc:  # noqa: BLE001 - speak() retries and falls back on its own
        ui.info(f"(голос {settings.tts_provider} пока недоступен: {exc})")
        return

    def _run() -> None:
        try:
            asyncio.run(warm_up())
        except Exception:  # noqa: BLE001 - a failed warm-up just means a slower first reply
            pass

    threading.Thread(target=_run, daemon=True).start()
    ui.info("(голос прогревается в фоне - если заговорить сразу, первый ответ будет дольше)")


async def speak(primary: TTSProvider, fallback: TTSProvider, text: str, ui: VoiceUI | None = None) -> None:
    """No timeout wraps the whole call on purpose: playback duration alone
    can legitimately exceed any fixed number for a long reply, and cutting
    it off mid-sentence just to start the fallback speaking the same text
    on top of it is worse than the problem (this actually happened - a
    15s ceiling here fired while a normal-length reply was still being
    played, so both voices spoke at once). Any timeout on the risky,
    genuinely-hangable part (a network TTS call) belongs inside that
    provider's own speak(), around just the synthesis step - see
    app/tts/edge.py.

    UI state: providers that announce playback (app.tts.playback -
    voicebox, piper) generate for a long time before any sound, so the UI
    stays on "thinking" and switches to its speaking animation when the
    audio actually starts. Others get "speaking" right away."""
    ui = ui or ConsoleUI()
    started = time.monotonic()
    if getattr(primary, "announces_playback", False):
        ui.state(THINKING, "Готовлю голос...")
    else:
        ui.state(SPEAKING)
    try:
        await primary.speak(text)
        ui.info(f"   (озвучено через {settings.tts_provider} за {time.monotonic() - started:.1f}с)")
    except Exception as exc:  # noqa: BLE001 - synthesis failing shouldn't kill the loop
        ui.info(f"(озвучка через {settings.tts_provider} не удалась: {exc} - пробую офлайн-голос)")
        ui.state(SPEAKING)
        await fallback.speak(text)


@dataclass
class VoiceSession:
    agent: Any
    tts_provider: TTSProvider | None
    tts_fallback: TTSProvider | None
    utterances: UtteranceLog | None
    resident_id: str
    ui: VoiceUI = field(default_factory=ConsoleUI)
    session_id: str = "voice-session"
    last_utterance_id: str | None = None
    # Set by run_hands_free: stops what's ringing/sounding; True if anything was.
    on_stop: Any = None
    # The reply being spoken right now - if it says "Джарвис" itself, hearing
    # the name doesn't count as being interrupted.
    speaking_text: str = ""


async def build_session(ui: VoiceUI, resident_id: str = "default") -> VoiceSession | None:
    """Everything either front end (terminal or window) needs to start."""
    if not settings.groq_api_key:
        ui.info("GROQ_API_KEY не задан в .env - он нужен для распознавания речи (Whisper), даже если LLM_PROVIDER не groq.")
        return None

    agent = build_default_agent()

    tts_provider = tts_fallback = None
    if settings.tts_enabled:
        from app.tts.sapi import SapiTTSProvider

        tts_fallback = SapiTTSProvider()
        try:
            tts_provider = build_tts_provider()
        except Exception as exc:  # noqa: BLE001 - e.g. piper model files not downloaded yet
            ui.info(f"Не удалось запустить {settings.tts_provider}: {exc}\nИспользую офлайн-голос вместо него.")
            tts_provider = tts_fallback
        await warm_up_in_background(tts_provider, ui)
    else:
        ui.info("Озвучка отключена (JARVIS_TTS_ENABLED=false) - Jarvis будет отвечать только текстом.")

    if settings.voice_id_enabled:
        speaker_id.configure(settings.speaker_model)
        # One-time model setup (seconds), off the first phrase's critical path.
        threading.Thread(target=_warm_up_speaker_id, daemon=True).start()

    utterances = UtteranceLog(settings.dataset_dir) if settings.dataset_enabled else None
    if utterances:
        stats = utterances.stats()
        ui.info(
            f"Датасет для обучения: {stats['utterances']} фраз, {stats['minutes']} мин аудио, "
            f"{stats['corrected']} исправлено ({settings.dataset_dir}/)"
        )

    return VoiceSession(
        agent=agent,
        tts_provider=tts_provider,
        tts_fallback=tts_fallback,
        utterances=utterances,
        resident_id=resident_id,
        ui=ui,
    )


def _warm_up_speaker_id() -> None:
    try:
        speaker_id.warm_up()
    except Exception:  # noqa: BLE001 - identify_or_enroll_speaker reports real failures itself
        pass


class Announcer:
    """What Jarvis says unasked - danger alarms (app.danger), timers and
    reminders (app.reminders): a sound, then the text, on a thread of its
    own. The offline Windows voice, not the reply voice: Voicebox takes
    ~25 s per phrase here, and an alarm or a timer can't wait - SAPI speaks
    at once. Separate from JARVIS_TTS_ENABLED on purpose: replies can be
    silent, a fire or the oven can't. DANGER_ALERT_VOICE=none leaves only
    the sounds. stop() ("стоп") cuts the sound and drops what's queued."""

    def __init__(self, speak: bool = True):
        self._speak = speak
        self._queue: "queue.Queue" = queue.Queue()
        self._generation = 0  # bumped by stop(): anything queued before is dropped
        self._playing = False
        threading.Thread(target=self._run, daemon=True).start()

    listeners: "weakref.WeakSet" = weakref.WeakSet()  # microphones to hold while it sounds

    def say(self, text: str | None, sound: str | None = None) -> None:
        """sound: "siren", "chime" or None."""
        self._queue.put((self._generation, text, sound))

    def say_alert(self, alert) -> None:
        self.say(alert.text, "siren" if alert.active else None)

    @property
    def busy(self) -> bool:
        return self._playing or not self._queue.empty()

    def stop(self) -> None:
        self._generation += 1
        _drain_queue(self._queue)
        try:
            import sounddevice as sd

            sd.stop()
        except Exception:  # noqa: BLE001 - nothing playing, or no audio device
            pass

    def _run(self) -> None:
        engine = None
        while True:
            generation, text, sound = self._queue.get()
            if generation != self._generation:
                continue  # stopped while it waited
            self._playing = True
            held = list(Announcer.listeners)
            for listener in held:
                listener.hold()
            try:
                if sound == "siren":
                    _play_siren()
                elif sound == "chime":
                    _play_chime()
                if self._speak and text and generation == self._generation:
                    if engine is None:
                        import pyttsx3

                        engine = pyttsx3.init()  # owned by this thread - SAPI is COM, thread-bound
                        from app.tts.sapi import pick_voice_id

                        voice_id = pick_voice_id(engine.getProperty("voices"), "ru")
                        if voice_id:
                            engine.setProperty("voice", voice_id)
                    engine.say(text)
                    engine.runAndWait()
            except Exception as exc:  # noqa: BLE001 - the banner is already up; sound is extra
                print(f"(объявление: звук не сработал - {exc!r})")
            finally:
                time.sleep(0.3)  # the room's echo
                for listener in held:
                    listener.release()
                self._playing = False


AlarmVoice = Announcer  # its first job was the danger alarm

_announcer: Announcer | None = None


def announcer() -> Announcer:
    """The one announcer, started on first use."""
    global _announcer
    if _announcer is None:
        _announcer = Announcer(speak=settings.danger_alert_voice != "none")
    return _announcer


def _drain_queue(q: "queue.Queue") -> None:
    while True:
        try:
            q.get_nowait()
        except queue.Empty:
            return


def _play_chime(rate: int = 22050) -> None:
    """Two soft bell-like notes - a timer, not an alarm."""
    import sounddevice as sd

    t = np.arange(int(0.45 * rate)) / rate
    notes = [0.35 * np.sin(2 * np.pi * f * t) * np.exp(-t * 6) for f in (1318.5, 1046.5)]
    sd.play(np.concatenate(notes).astype(np.float32), samplerate=rate, blocking=True)


def _play_siren(cycles: int = 3, rate: int = 22050) -> None:
    import sounddevice as sd

    t = np.arange(int(0.35 * rate)) / rate
    fade = np.minimum(1, np.minimum(t, t[::-1]) / 0.01)  # no clicks
    tones = [0.5 * np.sin(2 * np.pi * f * t) * fade for f in (880, 660)]
    sd.play(np.concatenate(tones * cycles).astype(np.float32), samplerate=rate, blocking=True)


def start_danger_watch(ui: VoiceUI):
    """Starts app.danger's watcher on a thread of its own, reporting to the
    UI and the alarm voice. None when there's no Home Assistant to watch."""
    if not settings.danger_alerts or not settings.home_assistant_token:
        return None
    from app.danger import DangerWatcher

    def on_alert(alert) -> None:
        ui.alert(alert.text, alert.key, alert.active)
        announcer().say_alert(alert)

    watcher = DangerWatcher(on_alert)
    threading.Thread(target=lambda: asyncio.run(watcher.run()), daemon=True).start()
    return watcher


def build_wake_detector(ui: VoiceUI) -> WakeWordDetector | None:
    try:
        return WakeWordDetector(settings.vosk_model_path, parse_wake_words(settings.wake_words))
    except Exception as exc:  # noqa: BLE001 - e.g. vosk or its model not installed
        ui.info(f"Распознавание имени недоступно ({exc}).")
        return None


def apply_correction(session: VoiceSession, typed: str, utterance_id: str | None = None) -> None:
    """utterance_id: a specific earlier phrase (the window's per-message
    edit button); None means the last one (typing in the terminal)."""
    target = utterance_id or session.last_utterance_id
    if session.utterances is None:
        session.ui.info("(запись датасета выключена - JARVIS_DATASET_ENABLED=false)")
    elif target and session.utterances.correct(target, typed):
        session.ui.info("Исправление сохранено в датасет.")
    else:
        session.ui.info("(исправлять нечего - в этом сеансе ещё не было ни одной фразы)")


def _failure_reply(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status == 429:
        try:
            per_day = "per day" in response.json()["error"]["message"]
            minutes = max(1, round(float(response.headers.get("retry-after", "60")) / 60))
        except Exception:  # noqa: BLE001 - an unexpected body: the generic answer
            per_day, minutes = False, 1
        if per_day:  # seen live: Groq's free 200 000 tokens a day ran out
            return f"Дневной лимит бесплатной модели исчерпан - снова смогу примерно через {minutes} мин."
        return "Лимит бесплатной модели на эту минуту кончился - повтори через минуту."
    return f"Не получилось ответить ({type(exc).__name__}). Повтори, пожалуйста."


async def _answer(session: VoiceSession, text: str) -> str:
    if is_stop_phrase(text) and session.on_stop is not None:
        # "Джарвис, стоп" - handled here, not by the model (no tokens, no wait).
        session.on_stop()
        session.ui.info("(остановила)")
        return ""
    session.ui.state(THINKING, "Думаю...")
    try:
        result = await session.agent.chat(session.session_id, session.resident_id, text, spoken=settings.tts_enabled)
    except Exception as exc:  # noqa: BLE001 - the model failing (rate limit, network) mustn't end the session
        reply = _failure_reply(exc)
        session.ui.info(f"(ошибка модели: {exc!r})")
        session.ui.jarvis_said(reply, [])
        return reply
    if result.get("local"):
        session.ui.info("(ответила своя модель)")
    session.ui.jarvis_said(result["response"], result["actions"])
    return result["response"]


async def handle_phrase(session: VoiceSession, frames: list[np.ndarray], prompt_for_name=None) -> None:
    """One spoken phrase, whichever mode captured it: speaker ID -> Whisper
    -> Jarvis -> dataset -> voice."""
    ui = session.ui
    # Before speaker ID too, not just before Whisper: an unmatched silent
    # clip would otherwise prompt "who are you?" and could get enrolled as
    # someone's voice.
    if not contains_speech(frames, SAMPLE_RATE):
        ui.info("(не услышал речи - ничего не отправляю)")
        return
    wav_bytes = frames_to_wav_bytes(frames)

    # The voice print is computed on a worker thread while Whisper runs -
    # ECAPA takes ~0.4 s per phrase on this laptop, and there's no reason
    # to wait for it before even starting the network call.
    embedding_task = None
    if settings.voice_id_enabled:
        embedding_task = asyncio.ensure_future(asyncio.to_thread(speaker_id.embed_wav_bytes, wav_bytes))

    ui.state(THINKING, "Распознаю...")
    prompt = build_whisper_prompt(settings.whisper_vocabulary, session.agent.memory.list_resident_ids())
    text: str | None = None
    try:
        text = await transcribe(wav_bytes, settings.groq_api_key, settings.groq_base_url, prompt=prompt)
    except Exception as exc:  # noqa: BLE001 - a failed request shouldn't kill the loop
        ui.info(f"Ошибка распознавания: {exc}")

    if text and is_hallucination(text):
        # Noise that got past the voice detector - not answered, and kept out
        # of the dataset, where a made-up transcript would be training data.
        if embedding_task is not None:
            embedding_task.cancel()
        ui.info(f"(похоже на шум - Whisper «услышал» «{text}», не отвечаю)")
        return

    decision: SpeakerDecision | None = None
    if embedding_task is not None:
        try:
            embedding = await embedding_task
        except Exception:  # noqa: BLE001 - identify_speaker retries and reports it
            embedding = None
        # Identification itself (database, maybe a question) stays on this
        # thread - the SQLite connection belongs to it.
        decision = identify_speaker(
            session.agent.memory,
            wav_bytes,
            session.resident_id,
            settings.voice_id_threshold,
            prompt_for_name=prompt_for_name,
            ui=ui,
            speech_seconds=speech_seconds(frames, SAMPLE_RATE),
            embedding=embedding,
        )
        session.resident_id = decision.resident

    # Logged right away - before answering - so the phrase can be corrected
    # the moment it's shown, and even when transcription failed or came back
    # empty: the audio is real speech (it passed the VAD check) and a typed
    # correction turns it into a usable training example.
    utterance_id: str | None = None
    if session.utterances:
        speaker_fields = {}
        if decision is not None:
            speaker_fields = {
                "speaker_how": decision.how,
                "speaker_score": decision.score,
                "voiceprint": str(decision.voiceprint) if decision.voiceprint else None,
            }
        utterance_id = session.utterances.log(
            wav_bytes=wav_bytes, resident_id=session.resident_id, transcript=text, **speaker_fields
        )
        session.last_utterance_id = utterance_id
    ui.user_said(
        text or "",
        voice=True,
        utterance_id=utterance_id,
        speaker=session.resident_id,
        speaker_sure=decision is None or decision.how in SURE,
    )
    if not text:
        return

    response = await _answer(session, text)
    if utterance_id:
        session.utterances.set_response(utterance_id, response)
    session.speaking_text = response
    if settings.tts_enabled and response:
        await speak(session.tts_provider, session.tts_fallback, text_for_speech(response), ui=ui)


async def handle_text(session: VoiceSession, text: str, echo: bool = True) -> None:
    """A typed message (the window's input box), or a corrected phrase being
    asked again (echo=False - its bubble is already on screen). No audio, so
    nothing to identify or log to the speech dataset."""
    if echo:
        session.ui.user_said(text, voice=False)
    response = await _answer(session, text)
    session.speaking_text = response
    if settings.tts_enabled and response:  # empty after "стоп"
        await speak(session.tts_provider, session.tts_fallback, text_for_speech(response), ui=session.ui)


def _play_listening_cue() -> None:
    try:
        import winsound

        winsound.Beep(880, 150)
    except Exception:  # noqa: BLE001 - no cue is fine, the UI still shows it
        pass


async def run_hands_free(
    session: VoiceSession, detector: WakeWordDetector | None, commands: "queue.Queue[tuple]"
) -> None:
    """The hands-free loop, shared by the terminal and the window. Besides
    the mic it takes commands from a queue, from whichever front end:
    ("text", message), ("correct", text, utterance_id | None, ask),
    ("wake",), ("toggle_mic",), ("enroll", name), ("enroll_cancel",),
    ("speaker", utterance_id, name), ("quit",).
    A correction is saved to the dataset and, with ask=True, also sent to
    Jarvis as what was really said - Jarvis answered the misheard version.
    While enrolling a voice, every phrase goes to that person's voice
    profile instead of being handled as a command (never to Whisper, never
    into the speech dataset). Without a detector (Vosk missing) the name
    can't be heard - waking up then only happens through ("wake",) or
    typing."""
    ui = session.ui
    memory = session.agent.memory
    wake_word = parse_wake_words(settings.wake_words)[0].capitalize()
    asleep_hint = f"Скажи «{wake_word}»" if detector else "Нажми на кружок, чтобы говорить"
    state = HandsFreeState(settings.wake_listen_seconds, settings.follow_up_seconds)
    enrolling: dict | None = None  # {"name", "got", "deadline"} while recording a voice
    mic_on = True  # the orb switches it: off = the stream really stops

    def idle() -> None:
        if mic_on:
            ui.state(SLEEPING, asleep_hint)
        else:
            ui.state(MUTED, "Микрофон выключен - нажми на кружок, чтобы говорить")

    async def listen_now() -> None:
        """Mic on (if it was off) and straight into listening, no name needed."""
        nonlocal mic_on
        if not mic_on:
            listener.resume()
            mic_on = True
        state.force_wake()
        await asyncio.to_thread(_play_listening_cue)
        ui.state(LISTENING, f"Слушаю ({settings.wake_listen_seconds:.0f} с)...")

    def finish_enrollment(status: str) -> None:
        nonlocal enrolling
        name, got = enrolling["name"], enrolling["got"]
        enrolling = None
        ui.enrollment(name, got, ENROLL_SAMPLES_NEEDED, status)
        report_voices(memory, ui)
        if got:
            session.resident_id = name  # whoever just enrolled is most likely the one talking
            ui.resident(name)
        idle()
    listener = HandsFreeListener(
        SAMPLE_RATE,
        on_level=ui.mic_level,
        # The name check runs while the phrase is still being spoken - see
        # app.wake_word.StreamingTranscript.
        transcriber_factory=detector.stream if detector else None,
    )
    Announcer.listeners.add(listener)

    # Timers and reminders (app.reminders): checked between phrases, on this
    # thread - the SQLite connection belongs to it.
    reminder_store = ReminderStore(memory.connection)
    ringing: dict[int, dict] = {}  # id -> {"text", "until", "next"}
    just_stopped = False
    interrupted = False  # the name was heard over an answer (respond)

    def ring_due() -> None:
        now = time.monotonic()
        for item in reminder_store.due(local_now()):
            reminder_store.mark_done(item["id"])
            ringing[item["id"]] = {"text": item["text"], "until": now + RING_SECONDS, "next": now + RING_EVERY_SECONDS}
            ui.ring(item["text"], f"reminder:{item['id']}", True)
            announcer().say(item["text"], "chime")
        for reminder_id, ring in list(ringing.items()):
            if now >= ring["until"]:  # nobody said stop - give up, keep it in the chat
                del ringing[reminder_id]
                ui.ring(ring["text"], f"reminder:{reminder_id}", False)
            elif now >= ring["next"]:
                ring["next"] = now + RING_EVERY_SECONDS
                announcer().say(None, "chime")

    def stop_all() -> bool:
        """"Стоп": silence whatever sounds, stop listening. True if anything was going on."""
        nonlocal just_stopped
        sounding = _announcer is not None and _announcer.busy
        stopped = bool(ringing) or sounding or state.is_awake()
        for reminder_id, ring in ringing.items():
            ui.ring(ring["text"], f"reminder:{reminder_id}", False)
        ringing.clear()
        if _announcer is not None:
            _announcer.stop()
        state.sleep()
        just_stopped = True
        idle()
        return stopped

    session.on_stop = stop_all

    async def respond(work) -> None:
        """Runs Jarvis's answer. Meanwhile the mic listens only for the name
        (barge-in): "Джарвис" - "Джарвис, стоп", "Джарвис, включи свет" -
        cuts the answer and Jarvis listens again; the command or a "стоп"
        that follows is handled as usual. Everything else heard meanwhile -
        above all Jarvis's own voice from the speakers - is dropped, never
        taken as the next phrase. Without Vosk there's no local transcript to
        check, so the mic is just muted."""
        nonlocal interrupted
        if detector is None:
            listener.mute()
        session.speaking_text = ""
        task = asyncio.ensure_future(work)
        try:
            while detector is not None and not task.done():
                await asyncio.sleep(0.1)
                heard = listener.live_text
                # a reply that says the name itself mustn't cut itself off
                own = name_heard(session.speaking_text, detector.wake_words)
                if not own and name_heard(heard, detector.wake_words):
                    task.cancel()
                    playback.stop_all()
                    interrupted = True
                    break
            try:
                await task
            except asyncio.CancelledError:
                pass
        finally:
            listener.mute()  # drops what was heard of our own voice
            await asyncio.sleep(0.3)  # let the room's echo of the reply die down
            listener.unmute()
        if interrupted:
            interrupted = False
            ui.info("(перебили - слушаю)")
            state.force_wake()
            ui.state(LISTENING, f"Слушаю ({settings.wake_listen_seconds:.0f} с)...")
            return
        nonlocal just_stopped
        if just_stopped:  # "стоп" - no follow-up listening after it
            just_stopped = False
            idle()
            return
        if mic_on:
            state.after_reply()
            ui.state(LISTENING, f"Слушаю ещё {settings.follow_up_seconds:.0f} с - можно без имени")
        else:
            idle()  # typed while the mic is off - it stays off

    idle()
    report_voices(memory, ui)
    try:
        while True:
            while True:
                try:
                    command = commands.get_nowait()
                except queue.Empty:
                    break
                kind = command[0]
                if kind == "quit":
                    return
                if kind == "enroll":
                    name = _existing_resident(memory, command[1]) if command[1].strip() else ""
                    if name:
                        if not mic_on:
                            listener.resume()
                            mic_on = True
                        enrolling = {"name": name, "got": 0, "deadline": time.monotonic() + ENROLL_TIMEOUT_SECONDS}
                        ui.enrollment(name, 0, ENROLL_SAMPLES_NEEDED, "started")
                        ui.state(LISTENING, "Читай фразы с экрана вслух")
                elif kind == "enroll_cancel":
                    if enrolling:
                        finish_enrollment("cancelled")
                elif kind == "speaker":
                    reassign_speaker(session, command[1], command[2])
                elif kind == "correct":
                    _, text, utterance_id, ask = command
                    if session.utterances is not None and (utterance_id or session.last_utterance_id):
                        apply_correction(session, text, utterance_id)
                    if ask:
                        await respond(handle_text(session, text, echo=False))
                elif kind == "stop":
                    stop_all()
                elif kind == "wake":
                    await listen_now()
                elif kind == "toggle_mic":
                    if mic_on:
                        listener.pause()
                        mic_on = False
                        state.sleep()
                        idle()
                    else:
                        await listen_now()
                elif kind == "text":
                    await respond(handle_text(session, command[1]))

            ring_due()
            if enrolling and time.monotonic() > enrolling["deadline"]:
                finish_enrollment("partial" if enrolling["got"] else "failed")

            was_awake = state.is_awake()
            phrase = await asyncio.to_thread(listener.next_phrase, 0.3)
            if phrase is None:
                if was_awake and not state.is_awake() and not enrolling:
                    idle()
                continue

            if enrolling:
                # Same thread on purpose: the SQLite connection belongs to it,
                # and embedding takes ~30 ms per piece.
                added = enroll_from_phrase(memory, enrolling["name"], phrase.frames)
                if added:
                    enrolling["got"] += added
                    if enrolling["got"] >= ENROLL_SAMPLES_NEEDED:
                        finish_enrollment("done")
                    else:
                        ui.enrollment(enrolling["name"], enrolling["got"], ENROLL_SAMPLES_NEEDED, "progress")
                continue

            # "Стоп" while something rings or Jarvis listens needs no name -
            # heard locally, never sent anywhere.
            something_on = ringing or (_announcer is not None and _announcer.busy) or state.is_awake()
            if something_on and is_stop_phrase(phrase.text):
                stop_all()
                continue

            wake_class = None
            if not state.is_awake() and detector is not None:
                # Local only - a phrase without the name never goes further.
                wake_class = classify(phrase.text or "", detector.wake_words)
            action = state.on_phrase(wake_class)
            if action == IGNORE:
                continue
            if action == CUE:
                await asyncio.to_thread(_play_listening_cue)
                ui.state(LISTENING, f"Слушаю ({settings.wake_listen_seconds:.0f} с)...")
                continue
            await respond(handle_phrase(session, phrase.frames))
    finally:
        listener.close()


# --- terminal front end ---


class ConsoleLineReader:
    """Typed lines in terminal hands-free mode: normally a correction of the
    last phrase; while a name is being asked for, the answer."""

    def __init__(self, commands: "queue.Queue[tuple]"):
        self._commands = commands
        self._names: queue.Queue[str] = queue.Queue()
        self._awaiting_name = threading.Event()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        while True:
            try:
                line = input().strip()
            except (EOFError, KeyboardInterrupt):
                return
            if self._awaiting_name.is_set():
                self._names.put(line)
            elif line:
                self._commands.put(("correct", line, None, True))

    def ask_name(self, prompt: str) -> str:
        print(prompt, end="", flush=True)
        self._awaiting_name.set()
        try:
            return self._names.get(timeout=60)
        except queue.Empty:
            print()
            return ""
        finally:
            self._awaiting_name.clear()


async def run_push_to_talk(session: VoiceSession) -> None:
    recorder = MicRecorder(SAMPLE_RATE)
    print(f"\nJarvis voice (режим с Enter) - resident '{session.resident_id}'. Ctrl+C для выхода.\n")
    try:
        while True:
            try:
                typed = input("[Enter] — говорить. Если я ошибся — впиши, что ты сказал, и Enter> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if typed:
                apply_correction(session, typed)
                continue
            print("🔴 Слушаю... нажмите Enter ещё раз, чтобы закончить")
            await handle_phrase(session, record_until_enter(recorder))
    finally:
        recorder.close()


async def main() -> None:
    console = ConsoleUI()
    try:
        resident_id = input("resident id (enter for 'default')> ").strip() or "default"
    except (EOFError, KeyboardInterrupt):
        resident_id = "default"

    session = await build_session(console, resident_id)
    if session is None:
        return
    start_danger_watch(console)

    if settings.voice_mode == "wake":
        detector = build_wake_detector(console)
        if detector is not None:
            commands: queue.Queue[tuple] = queue.Queue()
            console.ask = ConsoleLineReader(commands).ask_name
            wake_word = parse_wake_words(settings.wake_words)[0].capitalize()
            print(
                f"\nJarvis voice (без рук) - скажи «{wake_word}» и команду, или просто «{wake_word}» и жди сигнала.\n"
                "Пока имя не прозвучало, ничего не уходит в интернет и не сохраняется.\n"
                "Если я ошибся - впиши, что ты сказал, и Enter. Ctrl+C для выхода.\n"
            )
            await run_hands_free(session, detector, commands)
            return
        console.info("Включаю режим с Enter.")
    await run_push_to_talk(session)



if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
