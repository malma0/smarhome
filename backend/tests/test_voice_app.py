"""Only the pure/HTTP pieces of voice_app.py are covered here - actual
microphone recording (record_until_enter) needs a real audio device and
isn't something CI can exercise, so it's left as manually-verified glue."""

import asyncio
import json
import wave
from io import BytesIO
from unittest.mock import AsyncMock, Mock

import httpx
import numpy as np
import pytest

from app.db import connect
from app.memory import MemoryStore
from voice_app import (
    DEFAULT_WHISPER_VOCABULARY,
    MAX_WHISPER_PROMPT_CHARS,
    build_whisper_prompt,
    frames_to_wav_bytes,
    identify_or_enroll_speaker,
    transcribe,
    warm_up_in_background,
)


def test_empty_frames_produce_empty_bytes():
    assert frames_to_wav_bytes([]) == b""


def test_frames_produce_a_valid_wav_file():
    frames = [np.zeros((8000, 1), dtype=np.int16), np.ones((8000, 1), dtype=np.int16)]

    wav_bytes = frames_to_wav_bytes(frames, sample_rate=16000)

    with wave.open(BytesIO(wav_bytes), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 16000
        assert wf.getnframes() == 16000


def test_transcribe_posts_multipart_and_returns_stripped_text(monkeypatch):
    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"text": "  привет джарвис  "}

    mock_post = AsyncMock(return_value=_Resp())
    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    result = asyncio.run(transcribe(b"fake-wav-bytes", api_key="key", base_url="https://api.groq.com/openai/v1"))

    assert result == "привет джарвис"
    sent_kwargs = mock_post.call_args.kwargs
    assert sent_kwargs["data"]["model"] == "whisper-large-v3-turbo"
    assert sent_kwargs["files"]["file"][0] == "speech.wav"
    assert "prompt" not in sent_kwargs["data"]  # none given -> not sent at all


def test_transcribe_sends_the_vocabulary_prompt_when_given(monkeypatch):
    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"text": "открой Майнкрафт"}

    mock_post = AsyncMock(return_value=_Resp())
    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    asyncio.run(transcribe(b"wav", api_key="key", base_url="https://x", prompt="Джарвис, Майнкрафт"))

    assert mock_post.call_args.kwargs["data"]["prompt"] == "Джарвис, Майнкрафт"


# --- handle_phrase / apply_correction (the per-phrase pipeline both modes share) ---


class _FakeAgent:
    def __init__(self, memory):
        self.memory = memory
        self.chat = AsyncMock(return_value={"response": "Включаю.", "actions": []})


def _session(memory, tmp_path, dataset=True):
    import voice_app
    from app.dataset import UtteranceLog

    return voice_app.VoiceSession(
        agent=_FakeAgent(memory),
        tts_provider=None,
        tts_fallback=None,
        utterances=UtteranceLog(tmp_path / "ds") if dataset else None,
        resident_id="default",
    )


def _quiet_settings(monkeypatch):
    import dataclasses

    import voice_app

    monkeypatch.setattr(
        voice_app, "settings", dataclasses.replace(voice_app.settings, voice_id_enabled=False, tts_enabled=False)
    )


def test_silent_phrase_goes_nowhere(memory, tmp_path, monkeypatch):
    import voice_app

    _quiet_settings(monkeypatch)
    session = _session(memory, tmp_path)
    silence = [np.zeros((16000, 1), dtype=np.int16)]

    asyncio.run(voice_app.handle_phrase(session, silence))

    session.agent.chat.assert_not_called()
    assert session.utterances.stats()["utterances"] == 0


def test_a_phrase_is_transcribed_answered_and_logged(memory, tmp_path, monkeypatch):
    import voice_app

    _quiet_settings(monkeypatch)
    monkeypatch.setattr(voice_app, "contains_speech", lambda frames, sr: True)
    monkeypatch.setattr(voice_app, "transcribe", AsyncMock(return_value="включи свет"))
    session = _session(memory, tmp_path)

    asyncio.run(voice_app.handle_phrase(session, [np.ones((16000, 1), dtype=np.int16)]))

    session.agent.chat.assert_awaited_once_with("voice-session", "default", "включи свет", spoken=False)
    assert session.last_utterance_id is not None
    metadata = (tmp_path / "ds" / "metadata.jsonl").read_text("utf-8").splitlines()
    [record] = [json.loads(line) for line in metadata]
    assert record["transcript"] == "включи свет"
    assert record["response"] == "Включаю."


def test_correction_applies_to_the_last_phrase(memory, tmp_path, monkeypatch):
    import voice_app

    _quiet_settings(monkeypatch)
    monkeypatch.setattr(voice_app, "contains_speech", lambda frames, sr: True)
    monkeypatch.setattr(voice_app, "transcribe", AsyncMock(return_value="на нём видео"))
    session = _session(memory, tmp_path)
    asyncio.run(voice_app.handle_phrase(session, [np.ones((16000, 1), dtype=np.int16)]))

    voice_app.apply_correction(session, "включи на нём видео")

    assert session.utterances.stats()["corrected"] == 1


def test_correction_before_any_phrase_changes_nothing(memory, tmp_path):
    import voice_app

    session = _session(memory, tmp_path)
    voice_app.apply_correction(session, "что угодно")  # must not raise
    assert session.utterances.stats() == {"utterances": 0, "minutes": 0.0, "corrected": 0}


# --- what reaches the UI (the desktop window renders exactly these events) ---


class _RecordingUI:
    def __init__(self, name_answer=""):
        self.events = []
        self.name_answer = name_answer

    def state(self, state, detail=""):
        self.events.append(("state", state))

    def user_said(self, text, voice, utterance_id=None):
        self.events.append(("user", text, voice, utterance_id))

    def jarvis_said(self, text, actions):
        self.events.append(("jarvis", text))

    def info(self, text):
        self.events.append(("info", text))

    def resident(self, name):
        self.events.append(("resident", name))

    def mic_level(self, level):
        pass

    def speech_envelope(self, levels, frame_seconds):
        pass

    def ask_name(self, prompt, known=()):
        self.events.append(("ask_name", tuple(known)))
        return self.name_answer


def test_a_voice_phrase_is_shown_with_its_dataset_id_before_jarvis_answers(memory, tmp_path, monkeypatch):
    """The id comes with the phrase itself, so "correct this" is available
    the moment it's on screen - not only after a possibly long reply."""
    import voice_app

    _quiet_settings(monkeypatch)
    monkeypatch.setattr(voice_app, "contains_speech", lambda frames, sr: True)
    monkeypatch.setattr(voice_app, "transcribe", AsyncMock(return_value="включи свет"))
    session = _session(memory, tmp_path)
    session.ui = ui = _RecordingUI()

    asyncio.run(voice_app.handle_phrase(session, [np.ones((16000, 1), dtype=np.int16)]))

    assert [e[0] for e in ui.events] == ["state", "user", "state", "jarvis"]
    assert ui.events[1] == ("user", "включи свет", True, session.last_utterance_id)
    [record] = [json.loads(l) for l in (tmp_path / "ds" / "metadata.jsonl").read_text("utf-8").splitlines()]
    assert record["response"] == "Включаю."  # filled in after the answer


def test_an_unrecognized_phrase_still_gets_a_bubble_to_correct_but_no_answer(memory, tmp_path, monkeypatch):
    import voice_app

    _quiet_settings(monkeypatch)
    monkeypatch.setattr(voice_app, "contains_speech", lambda frames, sr: True)
    monkeypatch.setattr(voice_app, "transcribe", AsyncMock(return_value=""))
    session = _session(memory, tmp_path)
    session.ui = ui = _RecordingUI()

    asyncio.run(voice_app.handle_phrase(session, [np.ones((16000, 1), dtype=np.int16)]))

    assert ("user", "", True, session.last_utterance_id) in ui.events
    session.agent.chat.assert_not_called()


def test_a_typed_message_is_answered_but_not_logged_as_speech(memory, tmp_path, monkeypatch):
    import voice_app

    _quiet_settings(monkeypatch)
    session = _session(memory, tmp_path)
    session.ui = ui = _RecordingUI()

    asyncio.run(voice_app.handle_text(session, "который час?"))

    assert ("user", "который час?", False, None) in ui.events
    assert ("jarvis", "Включаю.") in ui.events
    assert session.utterances.stats()["utterances"] == 0  # no audio -> nothing for the speech dataset


def test_unknown_voice_asks_through_the_ui(memory, tmp_path, monkeypatch):
    import dataclasses

    import voice_app

    monkeypatch.setattr(voice_app, "settings", dataclasses.replace(voice_app.settings, tts_enabled=False))
    monkeypatch.setattr(voice_app, "contains_speech", lambda frames, sr: True)
    monkeypatch.setattr(voice_app, "speech_seconds", lambda frames, sr: 3.0)  # long enough to ask
    monkeypatch.setattr(voice_app, "transcribe", AsyncMock(return_value="привет"))
    monkeypatch.setattr("voice_app.speaker_id.embed_wav_bytes", lambda wav: "embedding")
    monkeypatch.setattr("voice_app.speaker_id.load_enrolled_voiceprints", lambda mem: {})
    monkeypatch.setattr("voice_app.speaker_id.identify_resident", lambda emb, enrolled, threshold: None)
    monkeypatch.setattr("voice_app.speaker_id.enroll_resident", Mock())
    session = _session(memory, tmp_path)
    session.ui = ui = _RecordingUI(name_answer="Матвей")

    asyncio.run(voice_app.handle_phrase(session, [np.ones((16000, 1), dtype=np.int16)]))

    assert ("ask_name", ()) in ui.events
    assert ("resident", "Матвей") in ui.events
    assert session.resident_id == "Матвей"


def test_correction_can_target_a_specific_earlier_phrase(memory, tmp_path, monkeypatch):
    import voice_app

    _quiet_settings(monkeypatch)
    monkeypatch.setattr(voice_app, "contains_speech", lambda frames, sr: True)
    monkeypatch.setattr(voice_app, "transcribe", AsyncMock(side_effect=["первая", "вторая"]))
    session = _session(memory, tmp_path)
    asyncio.run(voice_app.handle_phrase(session, [np.ones((16000, 1), dtype=np.int16)]))
    first_id = session.last_utterance_id
    asyncio.run(voice_app.handle_phrase(session, [np.ones((16000, 1), dtype=np.int16)]))

    voice_app.apply_correction(session, "первая, исправленная", first_id)

    lines = [json.loads(l) for l in (tmp_path / "ds" / "metadata.jsonl").read_text("utf-8").splitlines()]
    assert lines[0]["corrected_text"] == "первая, исправленная"
    assert lines[1]["corrected_text"] is None


def test_speak_waits_for_real_playback_before_showing_speaking_when_the_voice_announces_it():
    import voice_app

    class _AnnouncingVoice:
        announces_playback = True

        async def speak(self, text):
            pass

    class _PlainVoice:
        async def speak(self, text):
            pass

    for voice, expected in ((_AnnouncingVoice(), voice_app.THINKING), (_PlainVoice(), voice_app.SPEAKING)):
        ui = _RecordingUI()
        asyncio.run(voice_app.speak(voice, _PlainVoice(), "привет", ui=ui))
        assert ui.events[0] == ("state", expected)


# --- warm_up_in_background ---


def test_warm_up_runs_prepare_then_warm_up_on_another_thread():
    import threading

    warmed = threading.Event()
    calls = []

    class _Provider:
        async def prepare(self):
            calls.append(("prepare", threading.current_thread().name))

        async def warm_up(self):
            calls.append(("warm_up", threading.current_thread().name))
            warmed.set()

    asyncio.run(warm_up_in_background(_Provider()))

    assert warmed.wait(timeout=5)
    main = threading.main_thread().name
    assert calls[0] == ("prepare", main)  # before the thread: nothing can race profile creation
    assert calls[1][0] == "warm_up" and calls[1][1] != main


def test_warm_up_skipped_when_prepare_fails():
    warm = Mock()

    class _Provider:
        async def prepare(self):
            raise ConnectionError("voicebox not running")

        async def warm_up(self):
            warm()

    asyncio.run(warm_up_in_background(_Provider()))  # must not raise

    warm.assert_not_called()


def test_warm_up_is_a_no_op_for_providers_without_it():
    class _FastProvider:  # e.g. edge/sapi - nothing to warm up
        async def speak(self, text):
            pass

    asyncio.run(warm_up_in_background(_FastProvider()))  # must not raise


# --- build_whisper_prompt ---


def test_whisper_prompt_starts_with_the_default_vocabulary():
    prompt = build_whisper_prompt("", [])
    assert prompt == ", ".join(DEFAULT_WHISPER_VOCABULARY)


def test_whisper_prompt_adds_extra_words_and_resident_names_but_not_default():
    prompt = build_whisper_prompt(" Кухня , Алиса ,", ["Матвей", "default"])
    words = prompt.split(", ")
    assert words[-3:] == ["Кухня", "Алиса", "Матвей"]
    assert "default" not in words


def test_whisper_prompt_deduplicates_case_insensitively():
    prompt = build_whisper_prompt("джарвис, YOUTUBE", ["Матвей", "матвей"])
    words = [w.lower() for w in prompt.split(", ")]
    assert words.count("джарвис") == 1
    assert words.count("youtube") == 1
    assert words.count("матвей") == 1


def test_whisper_prompt_is_capped_at_a_whole_word():
    many = ", ".join(f"слово{i}" for i in range(200))
    prompt = build_whisper_prompt(many, [])
    assert len(prompt) <= MAX_WHISPER_PROMPT_CHARS
    assert prompt.split(", ")[-1].startswith("слово")  # last entry is a complete word, not a cut fragment
    assert not prompt.endswith(",")


# --- identify_or_enroll_speaker (app.speaker_id itself is mocked - no real
# embedding model or audio involved) ---


@pytest.fixture
def memory(tmp_path):
    return MemoryStore(connect(str(tmp_path / "test.db")))


def test_confident_match_is_returned_without_prompting(memory, monkeypatch):
    monkeypatch.setattr("voice_app.speaker_id.embed_wav_bytes", lambda wav: "embedding")
    monkeypatch.setattr("voice_app.speaker_id.load_enrolled_voiceprints", lambda mem: {"matvei": ["known"]})
    monkeypatch.setattr("voice_app.speaker_id.identify_resident", lambda emb, enrolled, threshold: "matvei")
    monkeypatch.setattr("voice_app.speaker_id.enroll_resident", Mock())
    prompt = Mock()

    result = identify_or_enroll_speaker(memory, b"wav", "default", 0.75, prompt_for_name=prompt)

    assert result == "matvei"
    prompt.assert_not_called()


def test_confident_match_also_feeds_the_recording_back_into_the_profile(memory, monkeypatch):
    """A successful match isn't just returned - it's fed back into
    speaker_id.enroll_resident so the profile keeps absorbing real usage
    instead of staying frozen at the first one-shot enrollment."""
    monkeypatch.setattr("voice_app.speaker_id.embed_wav_bytes", lambda wav: "embedding")
    monkeypatch.setattr("voice_app.speaker_id.load_enrolled_voiceprints", lambda mem: {"matvei": ["known"]})
    monkeypatch.setattr("voice_app.speaker_id.identify_resident", lambda emb, enrolled, threshold: "matvei")
    enroll = Mock()
    monkeypatch.setattr("voice_app.speaker_id.enroll_resident", enroll)

    identify_or_enroll_speaker(memory, b"wav", "default", 0.75, prompt_for_name=Mock())

    enroll.assert_called_once_with(memory, "matvei", "embedding")


def test_unrecognized_voice_enrolls_under_the_given_name(memory, monkeypatch):
    monkeypatch.setattr("voice_app.speaker_id.embed_wav_bytes", lambda wav: "embedding")
    monkeypatch.setattr("voice_app.speaker_id.load_enrolled_voiceprints", lambda mem: {})
    monkeypatch.setattr("voice_app.speaker_id.identify_resident", lambda emb, enrolled, threshold: None)
    enrolled_calls = []
    monkeypatch.setattr(
        "voice_app.speaker_id.enroll_resident",
        lambda mem, resident_id, emb: enrolled_calls.append((resident_id, emb)),
    )

    result = identify_or_enroll_speaker(
        memory, b"wav", "default", 0.75, prompt_for_name=lambda _prompt: "matvei"
    )

    assert result == "matvei"
    assert enrolled_calls == [("matvei", "embedding")]


def test_unrecognized_voice_declining_to_enroll_falls_back_to_default(memory, monkeypatch):
    monkeypatch.setattr("voice_app.speaker_id.embed_wav_bytes", lambda wav: "embedding")
    monkeypatch.setattr("voice_app.speaker_id.load_enrolled_voiceprints", lambda mem: {})
    monkeypatch.setattr("voice_app.speaker_id.identify_resident", lambda emb, enrolled, threshold: None)
    enroll = Mock()
    monkeypatch.setattr("voice_app.speaker_id.enroll_resident", enroll)

    result = identify_or_enroll_speaker(memory, b"wav", "default", 0.75, prompt_for_name=lambda _prompt: "")

    assert result == "default"
    enroll.assert_not_called()


def test_a_broken_embedding_step_falls_back_to_default_without_crashing(memory, monkeypatch):
    def _raise(wav):
        raise RuntimeError("model not installed")

    monkeypatch.setattr("voice_app.speaker_id.embed_wav_bytes", _raise)
    prompt = Mock()

    result = identify_or_enroll_speaker(memory, b"wav", "default", 0.75, prompt_for_name=prompt)

    assert result == "default"
    prompt.assert_not_called()


def _unmatched_voice(monkeypatch, enrolled=None):
    monkeypatch.setattr("voice_app.speaker_id.embed_wav_bytes", lambda wav: "embedding")
    monkeypatch.setattr("voice_app.speaker_id.load_enrolled_voiceprints", lambda mem: enrolled or {})
    monkeypatch.setattr("voice_app.speaker_id.identify_resident", lambda emb, enrolled, threshold: None)
    enroll = Mock()
    monkeypatch.setattr("voice_app.speaker_id.enroll_resident", enroll)
    return enroll


def test_short_unrecognized_phrase_keeps_the_last_speaker_without_asking(memory, monkeypatch):
    """~1 s of speech is too little to tell voices apart (real phrases like
    that scored 0.54-0.60 against their own speaker) - no "who are you?"
    interrupting a quick command, no enrollment."""
    enroll = _unmatched_voice(monkeypatch, {"Матвей": ["known"]})
    prompt = Mock()

    result = identify_or_enroll_speaker(memory, b"wav", "Матвей", 0.7, prompt_for_name=prompt, speech_seconds=0.9)

    assert result == "Матвей"
    prompt.assert_not_called()
    enroll.assert_not_called()


def test_short_phrase_can_still_match_but_does_not_teach_the_profile(memory, monkeypatch):
    monkeypatch.setattr("voice_app.speaker_id.embed_wav_bytes", lambda wav: "embedding")
    monkeypatch.setattr("voice_app.speaker_id.load_enrolled_voiceprints", lambda mem: {"Матвей": ["known"]})
    monkeypatch.setattr("voice_app.speaker_id.identify_resident", lambda emb, enrolled, threshold: "Матвей")
    enroll = Mock()
    monkeypatch.setattr("voice_app.speaker_id.enroll_resident", enroll)

    result = identify_or_enroll_speaker(memory, b"wav", "default", 0.7, speech_seconds=1.0)

    assert result == "Матвей"
    enroll.assert_not_called()


def test_answering_with_an_existing_name_in_any_case_adds_to_that_voice(memory, monkeypatch):
    """The fix for "it knows Матвей but didn't recognize me - now what?":
    answer with the same name, in whatever case, and the recording joins
    the existing profile instead of creating a second person."""
    memory.ensure_resident("Матвей")
    enroll = _unmatched_voice(monkeypatch, {"Матвей": ["known"]})

    result = identify_or_enroll_speaker(
        memory, b"wav", "default", 0.7, prompt_for_name=lambda _p: "  матвей ", speech_seconds=3.0
    )

    assert result == "Матвей"
    enroll.assert_called_once_with(memory, "Матвей", "embedding")
    assert memory.list_resident_ids().count("Матвей") == 1
    assert "матвей" not in memory.list_resident_ids()


def test_the_question_offers_the_known_voices(memory, monkeypatch):
    _unmatched_voice(monkeypatch, {"Матвей": ["a"], "Алиса": ["b"]})
    ui = _RecordingUI(name_answer="")

    identify_or_enroll_speaker(memory, b"wav", "default", 0.7, ui=ui, speech_seconds=3.0)

    assert ("ask_name", ("Алиса", "Матвей")) in ui.events


def test_declining_via_eof_falls_back_to_default(memory, monkeypatch):
    monkeypatch.setattr("voice_app.speaker_id.embed_wav_bytes", lambda wav: "embedding")
    monkeypatch.setattr("voice_app.speaker_id.load_enrolled_voiceprints", lambda mem: {})
    monkeypatch.setattr("voice_app.speaker_id.identify_resident", lambda emb, enrolled, threshold: None)

    def _eof(_prompt):
        raise EOFError

    result = identify_or_enroll_speaker(memory, b"wav", "default", 0.75, prompt_for_name=_eof)

    assert result == "default"
