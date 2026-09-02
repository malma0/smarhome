"""Tests for the tool-use loop itself, fully mocked: no real LLM API call and
no real Home Assistant. The agent only ever talks to LLMProvider/ToolRegistry/
MemoryStore interfaces, so a fake LLM and an in-memory-backed ToolRegistry are
enough - proving the core is genuinely device-agnostic (no tool needs to be
registered at all) and llm-agnostic (FakeLLM implements nothing Claude-specific)."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.agent import MAX_TOOL_ITERATIONS, JarvisAgent
from app.db import connect
from app.llm.base import ContentBlock, LLMResponse
from app.memory import MemoryStore
from app.persona import get_resident_gender, get_style
from app.tools.registry import Tool, ToolRegistry


class FakeLLM:
    """Duck-types LLMProvider - a real provider would call out to a vendor
    SDK inside generate(); this one just replays canned responses."""

    def __init__(self, responses: list[LLMResponse]):
        self.generate = AsyncMock(side_effect=responses)


def text_response(text: str, stop_reason: str = "end_turn") -> LLMResponse:
    return LLMResponse(content=[ContentBlock(type="text", text=text)], stop_reason=stop_reason)


def tool_use_response(id_: str, name: str, tool_input: dict, stop_reason: str = "tool_use") -> LLMResponse:
    return LLMResponse(
        content=[ContentBlock(type="tool_use", id=id_, name=name, input=tool_input)],
        stop_reason=stop_reason,
    )


@pytest.fixture
def memory(tmp_path):
    return MemoryStore(connect(str(tmp_path / "test.db")))


def make_agent(llm, memory, tools: ToolRegistry | None = None) -> JarvisAgent:
    return JarvisAgent(llm=llm, tools=tools or ToolRegistry(), memory=memory)


def test_plain_text_reply_without_any_tool_registered(memory):
    """Empty registry, general question - proves nothing home-related is needed."""
    llm = FakeLLM([text_response("Paris.")])
    agent = make_agent(llm, memory)

    result = asyncio.run(agent.chat("session-1", "ivan", "what's the capital of France?"))

    assert result == {"response": "Paris.", "actions": []}
    llm.generate.assert_awaited_once()


def test_tool_use_then_final_answer(memory):
    async def handler(tool_input, ctx):
        ctx.touched.add(tool_input["room"])
        return {"ok": True, "room": tool_input["room"], "light_on": True}

    tools = ToolRegistry()
    tools.register(Tool(name="set_room_lights", description="", parameters={}, handler=handler))

    llm = FakeLLM(
        [
            tool_use_response("call_1", "set_room_lights", {"room": "bedroom", "on": True}),
            text_response("Turned the bedroom lights on."),
        ]
    )
    agent = make_agent(llm, memory, tools)

    result = asyncio.run(agent.chat("session-1", "ivan", "turn on the bedroom lights"))

    assert result["response"] == "Turned the bedroom lights on."
    assert result["actions"] == [
        {
            "tool": "set_room_lights",
            "input": {"room": "bedroom", "on": True},
            "result": {"ok": True, "room": "bedroom", "light_on": True},
        }
    ]
    assert llm.generate.await_count == 2


def test_stops_after_max_tool_iterations(memory):
    async def handler(tool_input, ctx):
        return {"ok": True}

    tools = ToolRegistry()
    tools.register(Tool(name="get_home_status", description="", parameters={}, handler=handler))

    llm = FakeLLM([tool_use_response("call_x", "get_home_status", {})] * MAX_TOOL_ITERATIONS)
    agent = make_agent(llm, memory, tools)

    result = asyncio.run(agent.chat("session-1", "ivan", "status please"))

    assert "tool-call limit" in result["response"]
    assert llm.generate.await_count == MAX_TOOL_ITERATIONS
    assert len(result["actions"]) == MAX_TOOL_ITERATIONS


def test_persona_mode_shapes_the_system_prompt(memory):
    memory.set_persona_mode("butler")
    llm = FakeLLM([text_response("At your service.")])
    agent = make_agent(llm, memory)

    asyncio.run(agent.chat("session-1", "ivan", "hello"))

    system_prompt = llm.generate.call_args.kwargs["system"]
    assert "butler" in system_prompt.lower()


def test_adaptive_persona_updates_style_after_the_turn(memory):
    memory.set_persona_mode("adaptive")
    llm = FakeLLM([text_response("Understood.")])
    agent = make_agent(llm, memory)

    asyncio.run(agent.chat("session-1", "ivan", "Не могли бы Вы подсказать?"))

    assert get_style(memory, "ivan").formality > 0.5


def test_non_adaptive_persona_does_not_touch_style(memory):
    memory.set_persona_mode("warm")
    llm = FakeLLM([text_response("Sure thing!")])
    agent = make_agent(llm, memory)

    asyncio.run(agent.chat("session-1", "ivan", "Не могли бы Вы подсказать?"))

    assert get_style(memory, "ivan").formality == 0.5  # untouched default


def test_gender_revealed_in_the_message_is_stored_and_reaches_the_prompt(memory):
    llm = FakeLLM([text_response("Понял.")])
    agent = make_agent(llm, memory)

    asyncio.run(agent.chat("session-1", "ivan", "я сегодня сделала уборку"))

    assert get_resident_gender(memory, "ivan") == "female"
    system_prompt = llm.generate.call_args.kwargs["system"]
    assert "feminine" in system_prompt.lower()


def test_reply_with_gender_hedge_notation_is_resolved_before_returning(memory):
    llm = FakeLLM([text_response("Спасибо, что спросил(а)!")])
    agent = make_agent(llm, memory)

    result = asyncio.run(agent.chat("session-1", "ivan", "как дела?"))

    assert "(" not in result["response"]
    assert result["response"] == "Спасибо, что спросил!"
