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
    monkeypatch.setenv("JARVIS_TTS_ENABLED", "false")
    was = settings.tts_enabled
    try:
        assert people.set_voice(True, str(env))["enabled"] is True and settings.tts_enabled is True
        assert env.read_text(encoding="utf-8") == "GROQ_API_KEY=x\nJARVIS_TTS_ENABLED=true\n"
        people.set_voice(False, str(env))
        assert settings.tts_enabled is False and "JARVIS_TTS_ENABLED=false" in env.read_text(encoding="utf-8")
    finally:
        object.__setattr__(settings, "tts_enabled", was)  # the settings are frozen for everyone else
