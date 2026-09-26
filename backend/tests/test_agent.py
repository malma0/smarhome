"""Tests for the tool-use loop itself, fully mocked: no real LLM API call and
no real Home Assistant. The agent only ever talks to LLMProvider/ToolRegistry/
MemoryStore interfaces, so a fake LLM and an in-memory-backed ToolRegistry are
enough - proving the core is genuinely device-agnostic (no tool needs to be
registered at all) and llm-agnostic (FakeLLM implements nothing Claude-specific)."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.agent import MAX_TOOL_ITERATIONS, SPOKEN_REPLY_RULES, JarvisAgent
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


def test_spoken_replies_get_the_short_no_markdown_rules(memory):
    llm = FakeLLM([text_response("Готово."), text_response("Готово.")])
    agent = make_agent(llm, memory)

    asyncio.run(agent.chat("s1", "ivan", "открой блокнот", spoken=True))
    spoken_prompt = llm.generate.call_args.kwargs["system"]
    asyncio.run(agent.chat("s2", "ivan", "открой блокнот"))
    text_prompt = llm.generate.call_args.kwargs["system"]

    assert SPOKEN_REPLY_RULES in spoken_prompt
    assert SPOKEN_REPLY_RULES not in text_prompt  # text chat (e.g. POST /chat) unaffected


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


def test_only_the_latest_turns_are_sent_and_never_mid_turn():
    from app.agent import recent_turns

    history = []
    for n in range(4):
        history += [
            {"role": "user", "content": f"turn {n}"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": f"t{n}", "name": "x", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{n}", "content": "{}"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
        ]
    sent = recent_turns(history, 2)
    assert sent[0] == {"role": "user", "content": "turn 2"} and len(sent) == 8
    assert recent_turns(history, 10) is history



def test_the_model_is_told_the_current_local_time():
    from datetime import datetime, timedelta, timezone

    from app.agent import current_time_note

    now = datetime(2026, 9, 26, 7, 30, tzinfo=timezone(timedelta(hours=7)))
    assert current_time_note(now) == "Current local time: 2026-09-26 07:30, Saturday (UTC+0700)."



def test_a_cancelled_turn_leaves_no_half_done_history(memory):
    """"Джарвис, стоп" mid-answer: a tool call left without its result would
    make the provider reject the next request."""
    import asyncio

    from app.agent import JarvisAgent
    from app.llm.base import ContentBlock, LLMResponse
    from app.tools.registry import Tool, ToolRegistry

    started = asyncio.Event()

    async def slow_tool(tool_input, ctx):
        started.set()
        await asyncio.sleep(10)
        return {"ok": True}

    tools = ToolRegistry()
    tools.register(Tool(name="slow", description="", parameters={"type": "object"}, handler=slow_tool))
    llm = AsyncMock()
    llm.generate.side_effect = [
        LLMResponse(content=[ContentBlock(type="tool_use", id="t1", name="slow", input={})], stop_reason="tool_use"),
        LLMResponse(content=[ContentBlock(type="text", text="Готово.")], stop_reason="end_turn"),
    ]
    agent = JarvisAgent(llm=llm, tools=tools, memory=memory)

    async def go():
        task = asyncio.ensure_future(agent.chat("s", "r", "расскажи длинно"))
        await started.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return agent._sessions["s"]

    assert asyncio.run(go()) == []



def _tool(name):
    from app.tools.registry import Tool

    async def handler(tool_input, ctx):
        return {"ok": True}

    return Tool(name=name, description=name, parameters={"type": "object"}, handler=handler)


def test_only_the_tools_the_words_point_to_are_sent_and_follow_ups_keep_them(memory):
    import asyncio

    from app.agent import JarvisAgent
    from app.llm.base import ContentBlock, LLMResponse
    from app.tools.registry import ToolRegistry

    tools = ToolRegistry()
    for name in ("control_devices", "get_weather", "media"):
        tools.register(_tool(name))
    llm = AsyncMock()
    llm.generate.return_value = LLMResponse(content=[ContentBlock(type="text", text="Ок.")], stop_reason="end_turn")
    agent = JarvisAgent(llm=llm, tools=tools, memory=memory)

    def sent(message):
        asyncio.run(agent.chat("s", "r", message))
        return [t.name for t in llm.generate.call_args.kwargs["tools"]]

    assert sent("включи свет на кухне") == ["control_devices"]
    assert sent("да") == ["control_devices"]  # the follow-up is about the house too
    assert sent("какая погода?") == ["get_weather"]
    assert sent("привет") == ["get_weather"]  # no words to go on: the previous turn's


def test_the_clock_rides_on_the_message_not_the_system_prompt(memory):
    import asyncio

    from app.agent import JarvisAgent
    from app.llm.base import ContentBlock, LLMResponse
    from app.tools.registry import ToolRegistry

    llm = AsyncMock()
    llm.generate.return_value = LLMResponse(content=[ContentBlock(type="text", text="Ок.")], stop_reason="end_turn")
    agent = JarvisAgent(llm=llm, tools=ToolRegistry(), memory=memory)
    asyncio.run(agent.chat("s", "r", "напомни в 8"))
    system = llm.generate.call_args.kwargs["system"]
    message = llm.generate.call_args.kwargs["messages"][0]["content"]
    assert "Current local time" not in system  # the start of every request stays the same - Groq caches it
    assert message.startswith("напомни в 8\n\n[Current local time: ")
