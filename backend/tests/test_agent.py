"""Tests for the tool-use loop itself, fully mocked: no real call to the
Anthropic API and no real call to Home Assistant. We patch the pieces
app.agent.JarvisAgent.chat talks to (its Anthropic client, dispatch, and
build_system_prompt) with fakes that mimic just enough of their shape."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import app.agent as agent_module
from app.agent import JarvisAgent


def text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def tool_use_block(id_: str, name: str, tool_input: dict) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=tool_input)


def fake_response(content: list, stop_reason: str) -> SimpleNamespace:
    return SimpleNamespace(content=content, stop_reason=stop_reason)


def make_agent(monkeypatch) -> JarvisAgent:
    monkeypatch.setattr(agent_module, "build_system_prompt", AsyncMock(return_value="system prompt"))
    return JarvisAgent()


def test_plain_text_reply_without_tool_use(monkeypatch):
    agent = make_agent(monkeypatch)
    agent._client.messages.create = AsyncMock(
        return_value=fake_response([text_block("Hello!")], stop_reason="end_turn")
    )

    result = asyncio.run(agent.chat("session-1", "hi jarvis"))

    assert result == {"response": "Hello!", "actions": []}
    agent._client.messages.create.assert_awaited_once()


def test_tool_use_then_final_answer(monkeypatch):
    agent = make_agent(monkeypatch)
    fake_dispatch = AsyncMock(return_value={"ok": True, "room": "bedroom", "light_on": True})
    monkeypatch.setattr(agent_module, "dispatch", fake_dispatch)

    tool_call = tool_use_block("call_1", "set_room_lights", {"room": "bedroom", "on": True})
    agent._client.messages.create = AsyncMock(
        side_effect=[
            fake_response([tool_call], stop_reason="tool_use"),
            fake_response([text_block("Turned the bedroom lights on.")], stop_reason="end_turn"),
        ]
    )

    result = asyncio.run(agent.chat("session-1", "turn on the bedroom lights"))

    assert result["response"] == "Turned the bedroom lights on."
    assert result["actions"] == [
        {
            "tool": "set_room_lights",
            "input": {"room": "bedroom", "on": True},
            "result": {"ok": True, "room": "bedroom", "light_on": True},
        }
    ]
    fake_dispatch.assert_awaited_once()
    assert agent._client.messages.create.await_count == 2


def test_stops_after_max_tool_iterations(monkeypatch):
    agent = make_agent(monkeypatch)
    monkeypatch.setattr(agent_module, "dispatch", AsyncMock(return_value={"ok": True}))

    # The model never stops asking for tool calls - the loop must still
    # terminate instead of looping forever.
    tool_call = tool_use_block("call_x", "get_home_status", {})
    agent._client.messages.create = AsyncMock(
        return_value=fake_response([tool_call], stop_reason="tool_use")
    )

    result = asyncio.run(agent.chat("session-1", "status please"))

    assert "tool-call limit" in result["response"]
    assert agent._client.messages.create.await_count == agent_module.MAX_TOOL_ITERATIONS
    assert len(result["actions"]) == agent_module.MAX_TOOL_ITERATIONS
