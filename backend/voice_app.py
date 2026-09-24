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
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app import speaker_id
from app.agent import build_default_agent
from app.audio_capture import HandsFreeListener, MicRecorder, contains_speech
from app.config import settings
from app.dataset import UtteranceLog
from app.hands_free import CUE, IGNORE, HandsFreeState
from app.http_client import shared_client
from app.memory import MemoryStore
from app.tts.base import TTSProvider
from app.tts.text import text_for_speech
from app.voice_ui import LISTENING, SLEEPING, SPEAKING, THINKING, ConsoleUI, VoiceUI
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


def identify_or_enroll_speaker(
    memory: MemoryStore,
    wav_bytes: bytes,
    default_resident_id: str,
    threshold: float,
    prompt_for_name=input,
    ui: VoiceUI | None = None,
) -> str:
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
    first, one-shot enrollment happened to sound like."""
    ui = ui or ConsoleUI()
    try:
        embedding = speaker_id.embed_wav_bytes(wav_bytes)
    except Exception as exc:  # noqa: BLE001 - e.g. resemblyzer not installed, clip too short
        ui.info(f"(распознавание голоса недоступно: {exc})")
        return default_resident_id

    enrolled = speaker_id.load_enrolled_voiceprints(memory)
    match = speaker_id.identify_resident(embedding, enrolled, threshold=threshold)
    if match:
        speaker_id.enroll_resident(memory, match, embedding)
        ui.resident(match)
        return match

    try:
        name = prompt_for_name("Не узнал голос — как вас зовут? (Enter, чтобы не запоминать) ").strip()
    except (EOFError, KeyboardInterrupt):
        name = ""
    if not name:
        return default_resident_id

    speaker_id.enroll_resident(memory, name, embedding)
    ui.info(f"Запомнил ваш голос как «{name}».")
    ui.resident(name)
    return name


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
        # ~6s of one-time model setup, off the first phrase's critical path.
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


async def _answer(session: VoiceSession, text: str) -> str:
    session.ui.state(THINKING, "Думаю...")
    result = await session.agent.chat(session.session_id, session.resident_id, text, spoken=settings.tts_enabled)
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

    if settings.voice_id_enabled:
        session.resident_id = identify_or_enroll_speaker(
            session.agent.memory,
            wav_bytes,
            session.resident_id,
            settings.voice_id_threshold,
            prompt_for_name=prompt_for_name or ui.ask_name,
            ui=ui,
        )

    ui.state(THINKING, "Распознаю...")
    prompt = build_whisper_prompt(settings.whisper_vocabulary, session.agent.memory.list_resident_ids())
    text: str | None = None
    try:
        text = await transcribe(wav_bytes, settings.groq_api_key, settings.groq_base_url, prompt=prompt)
    except Exception as exc:  # noqa: BLE001 - a failed request shouldn't kill the loop
        ui.info(f"Ошибка распознавания: {exc}")

    # Logged right away - before answering - so the phrase can be corrected
    # the moment it's shown, and even when transcription failed or came back
    # empty: the audio is real speech (it passed the VAD check) and a typed
    # correction turns it into a usable training example.
    utterance_id: str | None = None
    if session.utterances:
        utterance_id = session.utterances.log(
            wav_bytes=wav_bytes, resident_id=session.resident_id, transcript=text
        )
        session.last_utterance_id = utterance_id
    ui.user_said(text or "", voice=True, utterance_id=utterance_id)
    if not text:
        return

    response = await _answer(session, text)
    if utterance_id:
        session.utterances.set_response(utterance_id, response)
    if settings.tts_enabled:
        await speak(session.tts_provider, session.tts_fallback, text_for_speech(response), ui=ui)


async def handle_text(session: VoiceSession, text: str, echo: bool = True) -> None:
    """A typed message (the window's input box), or a corrected phrase being
    asked again (echo=False - its bubble is already on screen). No audio, so
    nothing to identify or log to the speech dataset."""
    if echo:
        session.ui.user_said(text, voice=False)
    response = await _answer(session, text)
    if settings.tts_enabled:
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
    ("wake",), ("quit",). A correction is saved to the dataset and, with
    ask=True, also sent to Jarvis as what was really said - Jarvis answered
    the misheard version. Without a detector (Vosk missing) the name can't
    be heard - waking up then only happens through ("wake",) or typing."""
    ui = session.ui
    wake_word = parse_wake_words(settings.wake_words)[0].capitalize()
    asleep_hint = f"Скажи «{wake_word}»" if detector else "Нажми на кружок, чтобы говорить"
    state = HandsFreeState(settings.wake_listen_seconds, settings.follow_up_seconds)
    listener = HandsFreeListener(
        SAMPLE_RATE,
        on_level=ui.mic_level,
        # The name check runs while the phrase is still being spoken - see
        # app.wake_word.StreamingTranscript.
        transcriber_factory=detector.stream if detector else None,
    )

    async def respond(work) -> None:
        listener.mute()  # don't hear our own reply as the next phrase
        try:
            await work
        finally:
            await asyncio.sleep(0.3)  # let the room's echo of the reply die down
            listener.unmute()
        state.after_reply()
        ui.state(LISTENING, f"Слушаю ещё {settings.follow_up_seconds:.0f} с - можно без имени")

    ui.state(SLEEPING, asleep_hint)
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
                if kind == "correct":
                    _, text, utterance_id, ask = command
                    if session.utterances is not None and (utterance_id or session.last_utterance_id):
                        apply_correction(session, text, utterance_id)
                    if ask:
                        await respond(handle_text(session, text, echo=False))
                elif kind == "wake":
                    state.force_wake()
                    await asyncio.to_thread(_play_listening_cue)
                    ui.state(LISTENING, f"Слушаю ({settings.wake_listen_seconds:.0f} с)...")
                elif kind == "text":
                    await respond(handle_text(session, command[1]))

            was_awake = state.is_awake()
            phrase = await asyncio.to_thread(listener.next_phrase, 0.3)
            if phrase is None:
                if was_awake and not state.is_awake():
                    ui.state(SLEEPING, asleep_hint)
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
