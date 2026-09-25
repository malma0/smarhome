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


def test_a_whisper_hallucination_on_noise_is_not_answered_or_logged(memory, tmp_path, monkeypatch):
    """Seen live: room noise came back from Whisper as "Продолжение
    следует..." and Jarvis replied to it."""
    import voice_app

    _quiet_settings(monkeypatch)
    monkeypatch.setattr(voice_app, "contains_speech", lambda frames, sr: True)
    monkeypatch.setattr(voice_app, "transcribe", AsyncMock(return_value="Продолжение следует..."))
    session = _session(memory, tmp_path)

    asyncio.run(voice_app.handle_phrase(session, [np.ones((16000, 1), dtype=np.int16)]))

    session.agent.chat.assert_not_called()
    assert session.utterances.stats()["utterances"] == 0


def test_a_failing_model_gets_a_spoken_apology_not_a_crash(memory, tmp_path, monkeypatch):
    import httpx

    import voice_app

    _quiet_settings(monkeypatch)
    session = _session(memory, tmp_path)
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    rate_limited = httpx.HTTPStatusError("429", request=request, response=httpx.Response(429, request=request))
    session.agent.chat = AsyncMock(side_effect=rate_limited)

    reply = asyncio.run(voice_app._answer(session, "включи свет"))
    assert "Лимит" in reply

    session.agent.chat = AsyncMock(side_effect=httpx.ConnectError("offline"))
    assert "Не получилось ответить (ConnectError)" in asyncio.run(voice_app._answer(session, "включи свет"))


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

    def user_said(self, text, voice, utterance_id=None, speaker=None, speaker_sure=True):
        self.events.append(("user", text, voice, utterance_id))
        self.speakers = getattr(self, "speakers", []) + [(speaker, speaker_sure)]

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

    def voices(self, profiles):
        self.events.append(("voices", tuple(p["name"] for p in profiles)))

    def ring(self, text, key, active):
        self.events.append(("ring", text, active))

    def alert(self, text, key, active):
        self.events.append(("alert", text, active))

    def enrollment(self, name, collected, needed, status):
        self.events.append(("enroll", name, collected, status))


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
    monkeypatch.setattr("voice_app.speaker_id.match_resident_scored", lambda emb, enrolled, threshold: (None, False, None))
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
    monkeypatch.setattr("voice_app.speaker_id.match_resident_scored", lambda emb, enrolled, threshold: ("matvei", True, 0.8))
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
    monkeypatch.setattr("voice_app.speaker_id.match_resident_scored", lambda emb, enrolled, threshold: ("matvei", True, 0.8))
    enroll = Mock()
    monkeypatch.setattr("voice_app.speaker_id.enroll_resident", enroll)

    identify_or_enroll_speaker(memory, b"wav", "default", 0.75, prompt_for_name=Mock())

    enroll.assert_called_once_with(memory, "matvei", "embedding")


def test_unrecognized_voice_enrolls_under_the_given_name(memory, monkeypatch):
    monkeypatch.setattr("voice_app.speaker_id.embed_wav_bytes", lambda wav: "embedding")
    monkeypatch.setattr("voice_app.speaker_id.load_enrolled_voiceprints", lambda mem: {})
    monkeypatch.setattr("voice_app.speaker_id.match_resident_scored", lambda emb, enrolled, threshold: (None, False, None))
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
    monkeypatch.setattr("voice_app.speaker_id.match_resident_scored", lambda emb, enrolled, threshold: (None, False, None))
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
    monkeypatch.setattr("voice_app.speaker_id.match_resident_scored", lambda emb, enrolled, threshold: (None, False, None))
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
    monkeypatch.setattr("voice_app.speaker_id.match_resident_scored", lambda emb, enrolled, threshold: ("Матвей", True, 0.8))
    enroll = Mock()
    monkeypatch.setattr("voice_app.speaker_id.enroll_resident", enroll)

    result = identify_or_enroll_speaker(memory, b"wav", "default", 0.7, speech_seconds=1.0)

    assert result == "Матвей"
    enroll.assert_not_called()


def test_a_voice_clearly_closer_to_one_of_two_people_is_theirs_without_asking_or_learning(memory, monkeypatch):
    """Two people at home: a short phrase that doesn't clear the threshold
    but is clearly Матвей rather than Эля goes to Матвей - not to whoever
    spoke last, and not by interrupting with a question."""
    monkeypatch.setattr("voice_app.speaker_id.embed_wav_bytes", lambda wav: "embedding")
    monkeypatch.setattr("voice_app.speaker_id.load_enrolled_voiceprints", lambda mem: {"Матвей": ["a"], "Эля": ["b"]})
    monkeypatch.setattr("voice_app.speaker_id.match_resident_scored", lambda emb, enrolled, threshold: ("Матвей", False, 0.4))
    enroll = Mock()
    monkeypatch.setattr("voice_app.speaker_id.enroll_resident", enroll)
    prompt = Mock()

    for seconds in (0.9, 3.0):  # short or long - a relative call never asks, never teaches
        assert identify_or_enroll_speaker(memory, b"wav", "Эля", 0.7, prompt_for_name=prompt, speech_seconds=seconds) == "Матвей"
    prompt.assert_not_called()
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


# --- who said it: stored with each phrase, and fixable ("Кто говорил?") ---


def test_the_dataset_records_who_spoke_and_how_sure_jarvis_was(memory, tmp_path, monkeypatch):
    """The dialogue phrases are also training data for telling voices apart
    - so each one keeps how its speaker was decided, not just a name."""
    import dataclasses

    import voice_app

    monkeypatch.setattr(voice_app, "settings", dataclasses.replace(voice_app.settings, tts_enabled=False, voice_id_enabled=True))
    monkeypatch.setattr(voice_app, "contains_speech", lambda frames, sr: True)
    monkeypatch.setattr(voice_app, "speech_seconds", lambda frames, sr: 0.9)  # a short command
    monkeypatch.setattr(voice_app, "transcribe", AsyncMock(return_value="включи свет"))
    monkeypatch.setattr("voice_app.speaker_id.embed_wav_bytes", lambda wav: "embedding")
    monkeypatch.setattr("voice_app.speaker_id.load_enrolled_voiceprints", lambda mem: {"Матвей": ["a"], "Эля": ["b"]})
    monkeypatch.setattr("voice_app.speaker_id.match_resident_scored", lambda emb, enrolled, threshold: ("Матвей", False, 0.39))
    session = _session(memory, tmp_path)
    session.ui = ui = _RecordingUI()

    asyncio.run(voice_app.handle_phrase(session, [np.ones((16000, 1), dtype=np.int16)]))

    record = session.utterances.get(session.last_utterance_id)
    assert (record["resident_id"], record["speaker_how"], record["speaker_score"]) == ("Матвей", "closer", 0.39)
    assert record["voiceprint"] is None  # a guess on a short phrase never becomes a profile sample
    assert ui.speakers == [("Матвей", False)]  # shown as "Матвей?" - fixable


def _logged_phrase(session, resident, voiceprint=None):
    wav = frames_to_wav_bytes([np.ones((16000, 1), dtype=np.int16)])
    return session.utterances.log(
        wav_bytes=wav, resident_id=resident, transcript="привет", speaker_how="closer", voiceprint=voiceprint
    )


def test_fixing_the_speaker_moves_a_wrongly_learned_sample_and_rebuilds_both_profiles(memory, tmp_path, monkeypatch):
    import voice_app
    from app import speaker_id

    session = _session(memory, tmp_path)
    session.ui = _RecordingUI()
    wrong = speaker_id.save_sample_audio("Матвей", b"RIFF-sample")
    utterance_id = _logged_phrase(session, "Матвей", voiceprint=str(wrong))
    rebuilt = []
    monkeypatch.setattr("voice_app.speaker_id.rebuild_profiles", lambda mem, only=None, **kw: rebuilt.append(only))

    assert voice_app.reassign_speaker(session, utterance_id, "эля") is True  # any case -> existing/new "Эля"

    record = session.utterances.get(utterance_id)
    assert (record["resident_id"], record["speaker_how"], record["speaker_corrected_from"]) == ("эля", "corrected", "Матвей")
    assert not wrong.exists()
    assert speaker_id.sample_audio()["эля"][0].read_bytes() == b"RIFF-sample"
    assert rebuilt == [{"Матвей", "эля"}]
    assert session.resident_id == "эля"


def test_fixing_the_speaker_of_a_long_unlearned_phrase_adds_it_to_the_right_voice(memory, tmp_path, monkeypatch):
    import voice_app

    memory.ensure_resident("Эля")
    session = _session(memory, tmp_path)
    session.ui = _RecordingUI()
    utterance_id = _logged_phrase(session, "Матвей")
    monkeypatch.setattr(voice_app, "_wav_speech_seconds", lambda wav: 3.0)
    monkeypatch.setattr("voice_app.speaker_id.embed_wav_bytes", lambda wav: "embedding")
    enroll = Mock()
    monkeypatch.setattr("voice_app.speaker_id.enroll_resident", enroll)

    voice_app.reassign_speaker(session, utterance_id, "Эля")

    enroll.assert_called_once_with(memory, "Эля", "embedding")
    assert session.utterances.get(utterance_id)["voiceprint"]


def test_fixing_the_speaker_of_a_short_phrase_only_relabels_it(memory, tmp_path, monkeypatch):
    import voice_app

    session = _session(memory, tmp_path)
    session.ui = _RecordingUI()
    utterance_id = _logged_phrase(session, "Матвей")
    monkeypatch.setattr(voice_app, "_wav_speech_seconds", lambda wav: 0.9)
    enroll = Mock()
    monkeypatch.setattr("voice_app.speaker_id.enroll_resident", enroll)

    voice_app.reassign_speaker(session, utterance_id, "Эля")

    enroll.assert_not_called()
    assert session.utterances.get(utterance_id)["resident_id"] == "Эля"


# --- deliberate voice enrollment ("Записать голос") ---


def _seconds(n):
    return np.ones((int(n * 16000), 1), dtype=np.int16)


def test_enrollment_chunks_split_long_reading_and_drop_pieces_without_enough_speech(monkeypatch):
    import voice_app

    monkeypatch.setattr(voice_app, "speech_seconds", lambda frames, sr: len(frames[0]) / 16000)  # all "speech"
    assert [len(c[0]) / 16000 for c in voice_app.enrollment_chunks([_seconds(13)])] == [4.0, 4.0, 5.0]
    assert [len(c[0]) / 16000 for c in voice_app.enrollment_chunks([_seconds(3)])] == [3.0]  # short stays whole
    assert voice_app.enrollment_chunks([_seconds(1.5)]) == []  # under 2 s of speech - too little to learn from


def _fake_listener_factory(phrases, commands):
    from app.audio_capture import Phrase

    class _FakeListener:
        def __init__(self, sample_rate, on_level=None, transcriber_factory=None):
            self._phrases = [Phrase([p], None) for p in phrases]

        def next_phrase(self, timeout):
            if self._phrases:
                return self._phrases.pop(0)
            commands.put(("quit",))
            return None

        def mute(self): pass
        def unmute(self): pass
        def close(self): pass

        def pause(self):
            log.append("pause")

        def resume(self):
            log.append("resume")

    log = []
    _FakeListener.log = log
    return _FakeListener


def test_enrolling_a_voice_turns_the_next_phrases_into_samples_not_commands(memory, tmp_path, monkeypatch):
    import queue

    import voice_app

    commands = queue.Queue()
    commands.put(("enroll", "Эля"))
    monkeypatch.setattr(voice_app, "HandsFreeListener", _fake_listener_factory([_seconds(4)] * 3, commands))
    monkeypatch.setattr(voice_app, "enroll_from_phrase", lambda mem, name, frames: 1)
    handled = Mock()
    monkeypatch.setattr(voice_app, "handle_phrase", handled)
    session = _session(memory, tmp_path)
    session.ui = ui = _RecordingUI()

    asyncio.run(voice_app.run_hands_free(session, None, commands))

    enroll_events = [e for e in ui.events if e[0] == "enroll"]
    assert enroll_events == [
        ("enroll", "Эля", 0, "started"),
        ("enroll", "Эля", 1, "progress"),
        ("enroll", "Эля", 2, "progress"),
        ("enroll", "Эля", 3, "done"),
    ]
    handled.assert_not_called()  # the reading never went to Whisper or the dataset
    assert session.resident_id == "Эля"
    assert ("resident", "Эля") in ui.events


def test_enrollment_can_be_cancelled(memory, tmp_path, monkeypatch):
    import queue

    import voice_app

    commands = queue.Queue()
    commands.put(("enroll", "Эля"))
    commands.put(("enroll_cancel",))
    monkeypatch.setattr(voice_app, "HandsFreeListener", _fake_listener_factory([], commands))
    session = _session(memory, tmp_path)
    session.ui = ui = _RecordingUI()

    asyncio.run(voice_app.run_hands_free(session, None, commands))

    assert ("enroll", "Эля", 0, "cancelled") in ui.events
    assert session.resident_id == "default"


def test_declining_via_eof_falls_back_to_default(memory, monkeypatch):
    monkeypatch.setattr("voice_app.speaker_id.embed_wav_bytes", lambda wav: "embedding")
    monkeypatch.setattr("voice_app.speaker_id.load_enrolled_voiceprints", lambda mem: {})
    monkeypatch.setattr("voice_app.speaker_id.match_resident_scored", lambda emb, enrolled, threshold: (None, False, None))

    def _eof(_prompt):
        raise EOFError

    result = identify_or_enroll_speaker(memory, b"wav", "default", 0.75, prompt_for_name=_eof)

    assert result == "default"


def test_no_danger_watch_without_home_assistant_or_when_switched_off(monkeypatch):
    import dataclasses

    import voice_app

    monkeypatch.setattr(voice_app, "settings", dataclasses.replace(voice_app.settings, home_assistant_token=""))
    assert voice_app.start_danger_watch(_RecordingUI()) is None
    monkeypatch.setattr(
        voice_app, "settings", dataclasses.replace(voice_app.settings, home_assistant_token="t", danger_alerts=False)
    )
    assert voice_app.start_danger_watch(_RecordingUI()) is None


def test_the_orb_switches_the_mic_off_and_back_on_listening(memory, tmp_path, monkeypatch):
    import queue

    import voice_app

    monkeypatch.setattr(voice_app, "_play_listening_cue", lambda: None)
    commands = queue.Queue()
    commands.put(("toggle_mic",))
    listener = _fake_listener_factory([], commands)
    monkeypatch.setattr(voice_app, "HandsFreeListener", listener)
    session = _session(memory, tmp_path)
    session.ui = ui = _RecordingUI()

    asyncio.run(voice_app.run_hands_free(session, None, commands))
    assert listener.log == ["pause"] and ui.events[-1] == ("state", "muted")

    commands = queue.Queue()
    for command in (("toggle_mic",), ("toggle_mic",)):
        commands.put(command)
    listener = _fake_listener_factory([], commands)
    monkeypatch.setattr(voice_app, "HandsFreeListener", listener)
    session.ui = ui = _RecordingUI()
    asyncio.run(voice_app.run_hands_free(session, None, commands))
    assert listener.log == ["pause", "resume"] and ui.events[-1] == ("state", "listening")


def test_typing_while_the_mic_is_off_keeps_it_off(memory, tmp_path, monkeypatch):
    import queue

    import voice_app

    _quiet_settings(monkeypatch)
    commands = queue.Queue()
    commands.put(("toggle_mic",))
    commands.put(("text", "включи свет"))
    listener = _fake_listener_factory([], commands)
    monkeypatch.setattr(voice_app, "HandsFreeListener", listener)
    session = _session(memory, tmp_path)
    session.ui = ui = _RecordingUI()

    asyncio.run(voice_app.run_hands_free(session, None, commands))
    assert listener.log == ["pause"] and ui.events[-1] == ("state", "muted")



class _FakeAnnouncer:
    def __init__(self):
        self.said, self.stopped, self.busy = [], 0, False

    def say(self, text, sound=None):
        self.said.append((text, sound))

    def stop(self):
        self.stopped += 1


def _texts_listener(texts, commands):
    """A listener whose phrases carry a local transcript, like Vosk's."""
    from app.audio_capture import Phrase

    class _Listener:
        def __init__(self, sample_rate, on_level=None, transcriber_factory=None):
            self._phrases = [Phrase([_seconds(1)], t) for t in texts]

        def next_phrase(self, timeout):
            if self._phrases:
                return self._phrases.pop(0)
            commands.put(("quit",))
            return None

        def mute(self): pass
        def unmute(self): pass
        def close(self): pass
        def pause(self): pass
        def resume(self): pass

    return _Listener


def test_a_due_timer_rings_until_stop_is_said_without_the_name(memory, tmp_path, monkeypatch):
    import queue
    from datetime import timedelta

    import voice_app
    from app.reminders import ReminderStore, local_now

    fake = _FakeAnnouncer()
    monkeypatch.setattr(voice_app, "announcer", lambda: fake)
    monkeypatch.setattr(voice_app, "_announcer", fake)
    ReminderStore(memory.connection).add("timer", "Таймер на 10 минут", local_now() - timedelta(seconds=1))
    commands = queue.Queue()
    monkeypatch.setattr(voice_app, "HandsFreeListener", _texts_listener(["стоп"], commands))
    handled = Mock()
    monkeypatch.setattr(voice_app, "handle_phrase", handled)
    session = _session(memory, tmp_path)
    session.ui = ui = _RecordingUI()

    asyncio.run(voice_app.run_hands_free(session, None, commands))

    rings = [e for e in ui.events if e[0] == "ring"]
    assert rings == [("ring", "Таймер на 10 минут", True), ("ring", "Таймер на 10 минут", False)]
    assert fake.said[0] == ("Таймер на 10 минут", "chime") and fake.stopped == 1
    handled.assert_not_called()  # "стоп" never went to Whisper or the model
    assert ReminderStore(memory.connection).pending() == []


def test_stop_from_the_banner_button(memory, tmp_path, monkeypatch):
    import queue
    from datetime import timedelta

    import voice_app
    from app.reminders import ReminderStore, local_now

    fake = _FakeAnnouncer()
    monkeypatch.setattr(voice_app, "announcer", lambda: fake)
    monkeypatch.setattr(voice_app, "_announcer", fake)
    ReminderStore(memory.connection).add("reminder", "позвонить маме", local_now() - timedelta(seconds=1))
    commands = queue.Queue()

    class _Listener(_texts_listener([], commands)):
        calls = 0

        def next_phrase(self, timeout):
            _Listener.calls += 1
            commands.put(("stop",) if _Listener.calls == 1 else ("quit",))
            return None

    monkeypatch.setattr(voice_app, "HandsFreeListener", _Listener)
    session = _session(memory, tmp_path)
    session.ui = ui = _RecordingUI()

    asyncio.run(voice_app.run_hands_free(session, None, commands))

    assert [e for e in ui.events if e[0] == "ring"] == [("ring", "позвонить маме", True), ("ring", "позвонить маме", False)]


def test_stop_said_to_nothing_is_just_another_phrase(memory, tmp_path, monkeypatch):
    """Asleep, nothing ringing: "стоп" without the name is ignored like any phrase."""
    import queue

    import voice_app

    fake = _FakeAnnouncer()
    monkeypatch.setattr(voice_app, "_announcer", None)
    monkeypatch.setattr(voice_app, "announcer", lambda: fake)
    commands = queue.Queue()
    monkeypatch.setattr(voice_app, "HandsFreeListener", _texts_listener(["стоп"], commands))
    session = _session(memory, tmp_path)
    session.ui = ui = _RecordingUI()

    asyncio.run(voice_app.run_hands_free(session, None, commands))
    assert fake.stopped == 0


def test_stop_with_the_name_is_handled_here_not_by_the_model(memory, tmp_path):
    import voice_app

    session = _session(memory, tmp_path)
    stops = []
    session.on_stop = lambda: stops.append(1) or True
    assert asyncio.run(voice_app._answer(session, "Джарвис, стоп!")) == ""
    assert stops == [1]
    session.agent.chat.assert_not_called()



def _answering_listener(live_texts, commands):
    """No finished phrases; while Jarvis answers, "heard so far" goes
    through live_texts, then the loop is told to quit."""
    class _Listener(_texts_listener([], commands)):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._live = list(live_texts)

        @property
        def live_text(self):
            return self._live.pop(0) if self._live else ""

    return _Listener


class _Detector:
    wake_words = ["джарвис", "джервис"]

    def stream(self):
        return None


def _long_answer(finished, reply="Жили-были..."):
    async def answer(*args, **kwargs):
        await asyncio.sleep(10)
        finished.append(1)
        return {"response": reply, "actions": []}

    return answer


def test_the_name_over_an_answer_cuts_it_and_jarvis_listens(memory, tmp_path, monkeypatch):
    import queue

    import voice_app

    _quiet_settings(monkeypatch)
    cut, finished = [], []
    monkeypatch.setattr(voice_app.playback, "stop_all", lambda: cut.append(1))
    monkeypatch.setattr(voice_app, "_announcer", None)
    commands = queue.Queue()
    commands.put(("text", "расскажи длинную сказку"))
    # measured on a mix: the reply and "Джарвис, стоп" heard as one phrase
    monkeypatch.setattr(voice_app, "HandsFreeListener",
                        _answering_listener(["жили были старик", "синего моря джарвис рик"], commands))
    session = _session(memory, tmp_path)
    session.agent.chat = _long_answer(finished)
    session.ui = ui = _RecordingUI()

    asyncio.run(voice_app.run_hands_free(session, _Detector(), commands))

    assert finished == [] and cut == [1]
    assert ("info", "(перебили - слушаю)") in ui.events
    assert ("state", "listening") in ui.events[-3:]


def test_without_the_name_the_answer_runs_to_the_end(memory, tmp_path, monkeypatch):
    import queue

    import voice_app

    _quiet_settings(monkeypatch)
    finished = []
    monkeypatch.setattr(voice_app, "_announcer", None)
    commands = queue.Queue()
    commands.put(("text", "привет"))
    monkeypatch.setattr(voice_app, "HandsFreeListener", _answering_listener(["стоп хватит"], commands))
    session = _session(memory, tmp_path)

    async def short_answer(*args, **kwargs):
        await asyncio.sleep(0.3)
        finished.append(1)
        return {"response": "Привет!", "actions": []}

    session.agent.chat = short_answer
    session.ui = _RecordingUI()
    asyncio.run(voice_app.run_hands_free(session, _Detector(), commands))
    assert finished == [1]  # "стоп" from the speakers alone doesn't cut it



def test_the_daily_limit_is_told_apart_from_the_minute_one(memory, tmp_path, monkeypatch):
    import httpx

    import voice_app

    _quiet_settings(monkeypatch)
    session = _session(memory, tmp_path)
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    body = {"error": {"message": "Rate limit reached ... on tokens per day (TPD): Limit 200000, Used 198720"}}
    response = httpx.Response(429, request=request, json=body, headers={"retry-after": "541"})
    session.agent.chat = AsyncMock(side_effect=httpx.HTTPStatusError("429", request=request, response=response))
    assert asyncio.run(voice_app._answer(session, "сделай скриншот")) == (
        "Дневной лимит бесплатной модели исчерпан - снова смогу примерно через 9 мин.")
