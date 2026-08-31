from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_voice_page_served():
    resp = client.get("/voice")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Jarvis" in resp.text
