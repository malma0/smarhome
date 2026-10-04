"""Settings' people: residents and their voices, guests' codes that open the panel for a while
but not its setup, and Jarvis answering aloud or not."""

import base64
import io
import wave
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import people
from app.config import settings
from app.db import connect
from app.memory import MemoryStore
from app.panel import create_app
from tests.test_home_features import FakeHA

PIN = "test-pin-1234"
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def guests_file(tmp_path, monkeypatch):
    from app import speaker_id

    monkeypatch.setattr(people, "GUESTS_FILE", tmp_path / "guests.json")
    monkeypatch.setattr(speaker_id, "VOICEPRINTS_DIR", tmp_path / "voiceprints")  # never the home's own recordings


class _Agent:
    def __init__(self, db):
        self.memory = MemoryStore(connect(db))  # made on the server's thread, like the real one


@pytest.fixture
def client(tmp_path):
    db = str(tmp_path / "t.db")
    with TestClient(create_app(client=FakeHA(), pin=PIN, agent_factory=lambda: _Agent(db))) as c:  # one server thread
        yield c


def test_a_guest_code_lasts_its_hours_and_is_never_the_owners_pin():
    g = people.add_guest("  Бабушка ", 4, NOW, "123456")
    assert g["name"] == "Бабушка" and len(g["code"]) == 6 and g["code"].isdigit() and g["code"] != "123456"
    assert people.guest_by_code(g["code"], NOW + timedelta(hours=3))["name"] == "Бабушка"
    assert people.guest_by_code(g["code"], NOW + timedelta(hours=4, minutes=1)) is None  # over: gone from the file too
    assert people.guests(NOW) == []
    with pytest.raises(ValueError):
        people.add_guest("Бабушка", 5, NOW, "")  # only the editor's choices
    with pytest.raises(ValueError):
        people.add_guest(" ", 4, NOW, "")
    assert people.guest_by_code("", NOW) is None


def test_a_guest_switches_things_but_does_not_change_the_house(client):
    owner = {"X-Pin": PIN}
    made = client.post("/api/guests", headers=owner, json={"name": "Лёша", "hours": 24}).json()
    code = made["guest"]["code"]
    assert [g["name"] for g in made["guests"]] == ["Лёша"]
    guest = {"X-Pin": code}
    assert client.get("/api/ping", headers=guest).json()["guest"]["name"] == "Лёша"
    assert client.get("/api/ping", headers=owner).json()["guest"] is None
    assert client.get("/api/house", headers=guest).status_code == 200
    for method, path in [("GET", "/api/people"), ("POST", "/api/scenes"), ("POST", "/api/schedules"),
                         ("PUT", "/api/layout"), ("POST", "/api/automation"), ("POST", "/api/guests"),
                         ("POST", "/api/voice-settings"), ("DELETE", "/api/scenes/x")]:
        assert client.request(method, path, headers=guest, json={}).status_code == 403, path
    assert client.delete("/api/guests/" + code, headers=owner).json()["guests"] == []
    assert client.get("/api/ping", headers=guest).status_code == 401  # revoked: the code no longer opens anything
    assert "error" in client.post("/api/guests", headers=owner, json={"name": "Лёша", "hours": 2}).json()


def _phrase(seconds=2.0):
    t = np.arange(int(16000 * seconds)) / 16000
    audio = (np.sin(2 * np.pi * 180 * t) * 8000).astype(np.int16)
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(audio.tobytes())
    return out.getvalue()


def test_residents_are_added_and_learn_their_voice_from_the_phone(client, monkeypatch):
    import voice_app

    learned = []
    monkeypatch.setattr(voice_app, "enroll_from_phrase", lambda mem, name, chunks: learned.append((name, len(chunks[0]))) or 3)
    owner = {"X-Pin": PIN}
    got = client.post("/api/residents", headers=owner, json={"name": " Эля "}).json()
    assert [r["name"] for r in got["residents"]] == ["Эля"] and got["residents"][0]["samples"] == 0
    assert len(got["read_lines"]) == 3 and "{name}" in got["read_lines"][0]
    assert "error" in client.post("/api/residents", headers=owner, json={"name": "эля"}).json()  # already there
    added = client.post("/api/residents/voice", headers=owner,
                        json={"name": "Эля", "audio": base64.b64encode(_phrase()).decode()}).json()
    assert added["added"] == 3 and learned == [("Эля", 32000)]
    assert "error" in client.post("/api/residents/voice", headers=owner,
                                  json={"name": "Кто-то", "audio": base64.b64encode(_phrase()).decode()}).json()
    assert "error" in client.post("/api/residents/voice", headers=owner, json={"name": "Эля", "audio": "не base64"}).json()
    assert client.delete("/api/residents/Эля", headers=owner).json()["residents"] == []
    assert "error" in client.delete("/api/residents/panel", headers=owner).json()  # Jarvis's own ids stay


def test_jarvis_answers_aloud_or_not_at_once_and_after_a_restart(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("GROQ_API_KEY=x\nJARVIS_TTS_ENABLED=false\n", encoding="utf-8")
    import voice_app

    started = []
    monkeypatch.setattr(voice_app, "start_voicebox_if_needed", lambda: started.append(1))  # never the real server
    monkeypatch.setenv("JARVIS_TTS_ENABLED", "false")
    was = settings.tts_enabled
    try:
        assert people.set_voice(True, str(env))["enabled"] is True and settings.tts_enabled is True
        assert env.read_text(encoding="utf-8") == "GROQ_API_KEY=x\nJARVIS_TTS_ENABLED=true\n"
        people.set_voice(False, str(env))
        assert settings.tts_enabled is False and "JARVIS_TTS_ENABLED=false" in env.read_text(encoding="utf-8")
        import time

        time.sleep(0.2)
        assert started == [1]  # switched on: the voice server warmed up at once, off: left alone
    finally:
        object.__setattr__(settings, "tts_enabled", was)  # the settings are frozen for everyone else


def test_the_phones_get_the_reminders_that_rang_at_home(client, tmp_path):
    from app.reminders import ReminderStore

    owner = {"X-Pin": PIN}
    assert client.get("/api/alerts", headers=owner).json()["reminders"] == []
    store = ReminderStore(connect(str(tmp_path / "t.db")))  # the voice loop's own connection to the same file
    now = datetime.now().astimezone()
    store.rang(store.add("reminder", "выключить духовку", now), now)
    rings = client.get("/api/alerts", headers=owner).json()["reminders"]
    assert [(r["kind"], r["text"]) for r in rings] == [("reminder", "выключить духовку")] and rings[0]["id"] >= 1


def test_each_phone_gets_its_owners_reminders_and_everyones(client, tmp_path):
    from app.reminders import ReminderStore

    owner = {"X-Pin": PIN}
    store = ReminderStore(connect(str(tmp_path / "t.db")))
    now = datetime.now().astimezone()
    for text, who in [("таблетки", "Матвей"), ("хлеб", "Эля"), ("мусор", "default")]:
        store.rang(store.add("reminder", text, now, resident_id=who), now)
    texts = lambda q: [r["text"] for r in client.get("/api/alerts" + q, headers=owner).json()["reminders"]]  # noqa: E731
    assert texts("") == ["таблетки", "хлеб", "мусор"]  # a phone nobody's: all of them
    assert texts("?who=Эля") == ["хлеб", "мусор"]


def test_the_voices_shown_are_the_model_jarvis_recognizes_with(tmp_path):
    from app import speaker_id

    memory = MemoryStore(connect(str(tmp_path / "v.db")))
    memory.ensure_resident("Эля")
    was = settings.speaker_model
    object.__setattr__(settings, "speaker_model", "ecapa")
    try:
        speaker_id.configure("ecapa")
        speaker_id.enroll_resident(memory, "Эля", np.ones(192))
        speaker_id.configure("resemblyzer")  # as the panel started: the default, old model
        assert people.residents(memory) == [{"name": "Эля", "samples": 1}]
        assert speaker_id.active_model() is speaker_id.MODELS["ecapa"]
    finally:
        object.__setattr__(settings, "speaker_model", was)
        speaker_id.configure("resemblyzer")


def test_the_app_sees_sets_and_cancels_reminders_and_the_shopping_list(client, tmp_path):
    from app.reminders import ReminderStore

    owner = {"X-Pin": PIN}
    client.post("/api/residents", headers=owner, json={"name": "Эля"})
    at = (datetime.now().astimezone() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    made = client.post("/api/reminders", headers=owner, json={"text": "купить хлеб", "at": at, "who": "Эля"}).json()
    assert made["added"]["for"] == "Эля" and [r["text"] for r in made["reminders"]] == ["купить хлеб"]
    client.post("/api/reminders", headers=owner, json={"kind": "timer", "in_seconds": 600})
    assert "error" in client.post("/api/reminders", headers=owner, json={"text": "x", "at": "2020-01-01T08:00"}).json()
    seen = client.get("/api/reminders?who=Эля", headers=owner).json()
    assert [t["text"] for t in seen["timers"]] == ["Таймер на 10 минут"] and len(seen["reminders"]) == 1
    rid = seen["reminders"][0]["id"]
    assert client.delete(f"/api/reminders/{rid}", headers=owner).json()["reminders"] == []
    # "Стоп" from the phone
    store = ReminderStore(connect(str(tmp_path / "t.db")))
    ring = store.rang(store.pending()[0], datetime.now().astimezone())
    assert client.post("/api/reminders/stop", headers=owner, json={"ring": ring}).json() == {"stopped": True}
    assert store.stopped_rings([ring]) == {ring}
    # the shopping list: who added it and how, a tick takes that one line only
    items = lambda r: [i["item"] for i in r["items"]]  # noqa: E731
    added = client.post("/api/shopping", headers=owner, json={"action": "add", "items": ["хлеб", "хлеб бородинский"], "who": "Эля"}).json()
    assert added["items"][0] == {"item": "хлеб", "who": "Эля", "via": "app"}
    assert items(client.post("/api/shopping", headers=owner, json={"action": "bought", "items": ["хлеб"]}).json()) == ["хлеб бородинский"]
    assert items(client.get("/api/shopping", headers=owner).json()) == ["хлеб бородинский"]
    assert client.post("/api/shopping", headers=owner, json={"action": "clear"}).json()["items"] == []
