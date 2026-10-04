import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app.llm.base import ContentBlock, LLMResponse, ToolDef
from app.llm.home_service import HomeServiceProvider
from model_service import app as service
from model_service.inference import OllamaBackend, Turn


class FakeBackend(OllamaBackend):
    """The model replaced: answers with a tool call (or fails), records what it was asked."""

    def __init__(self, fail=False, available=True, malformed=0):
        super().__init__("jarvis-home-test", "http://ollama.test")
        self.fail, self.available, self.malformed = fail, available, malformed
        self.asked = []

    async def turn(self, system, messages, tools):
        self.asked.append((system, messages, [t.name for t in tools]))
        if self.fail:
            raise httpx.ConnectError("ollama is off")
        call = ContentBlock(type="tool_use", id="call_1", name="control_devices",
                            input={"room": "кухня", "device": "light", "action": "off"})
        return Turn(response=LLMResponse(content=[call], stop_reason="tool_use"), raw="<tool_call>...</tool_call>",
                    seconds=0.25, inference_seconds=0.2, prompt_tokens=1800, completion_tokens=30,
                    malformed_calls=self.malformed, timings={"eval_seconds": 0.15})

    async def model_info(self):
        return {"available": True, "digest": "abc123", "quantization": "Q8_0"} if self.available \
            else {"available": False, "error": "Ollama has no model jarvis-home-test:latest"}


@pytest.fixture
def make_client(monkeypatch):
    monkeypatch.setattr(service, "registry_version", lambda model: {"version": 6, "alias": "production"})

    def make(**kwargs):
        backend = FakeBackend(**kwargs)
        return TestClient(service.create_app(backend)), backend

    return make


def _metric(client, line_start):
    lines = [l for l in client.get("/metrics").text.splitlines() if l.startswith(line_start)]
    return float(lines[0].rsplit(" ", 1)[1]) if lines else 0.0


def test_generate_returns_the_models_turn_and_counts_it(make_client):
    client, backend = make_client(malformed=1)
    with client:
        answer = client.post("/v1/generate", json={
            "system": "You are Jarvis", "messages": [{"role": "user", "content": "выключи свет на кухне"}],
            "tools": [{"name": "control_devices", "description": "...", "parameters": {"type": "object"}}],
        }).json()
        assert answer["stop_reason"] == "tool_use"
        assert answer["content"][0]["name"] == "control_devices"
        assert answer["timing"]["total_ms"] == 250.0 and answer["timing"]["tokens_per_second"] == 200.0
        assert backend.asked[0][2] == ["control_devices"]
        assert _metric(client, 'home_model_requests_total{endpoint="generate",outcome="ok"}') == 1
        assert _metric(client, 'home_model_tool_calls_total{tool="control_devices"}') == 1
        assert _metric(client, "home_model_malformed_tool_calls_total") == 1
        assert _metric(client, 'home_model_tokens_total{kind="prompt"}') == 1800


def test_command_sends_jarvis_prompt_and_the_trained_tools(make_client):
    client, backend = make_client()
    with client:
        answer = client.post("/v1/command", json={"text": "Джарвис, выключи свет на кухне"}).json()
    assert answer["tool_calls"] == [{"name": "control_devices",
                                     "arguments": {"room": "кухня", "device": "light", "action": "off"}}]
    system, messages, tools = backend.asked[0]
    assert system.startswith("You are Jarvis")
    assert messages[0]["content"].startswith("Джарвис, выключи свет на кухне\n\n[Current local time:")
    assert "control_devices" in tools and "get_home_status" in tools


def test_the_model_down_is_a_503_counted_as_an_upstream_error(make_client):
    client, _ = make_client(fail=True)
    with client:
        response = client.post("/v1/command", json={"text": "включи свет"})
        assert response.status_code == 503
        assert _metric(client, 'home_model_requests_total{endpoint="command",outcome="upstream_error"}') == 1
        assert client.get("/v1/stats").json()["command"]["errors"] == 1


def test_health_and_model_say_what_is_served(make_client):
    client, _ = make_client()
    with client:
        assert client.get("/health").json() == {"status": "ok", "model": "jarvis-home-test"}
        model = client.get("/v1/model").json()
        assert model["digest"] == "abc123" and model["version"] == 6 and model["alias"] == "production"
    down, _ = make_client(available=False)
    with down:
        assert down.get("/health").status_code == 503


def test_stats_give_latency_percentiles(make_client):
    client, _ = make_client()
    with client:
        for _ in range(5):
            client.post("/v1/command", json={"text": "включи свет"})
        stats = client.get("/v1/stats").json()["command"]
    assert stats["requests"] == 5 and stats["errors"] == 0 and stats["p95_ms"] == 250.0


def test_jarvis_through_the_service_gets_the_same_turn_back(make_client):
    """app/llm/home_service.py - what JarvisAgent uses when HOME_LLM_SERVICE_URL is set."""
    client, _ = make_client()
    with client:
        provider = HomeServiceProvider("http://service.test", transport=httpx.ASGITransport(app=client.app))
        response = asyncio.run(provider.generate(
            system="You are Jarvis", messages=[{"role": "user", "content": "выключи свет на кухне"}],
            tools=[ToolDef(name="control_devices", description="...", parameters={"type": "object"})]))
    assert response.stop_reason == "tool_use"
    assert response.content[0].name == "control_devices" and response.content[0].input["room"] == "кухня"
