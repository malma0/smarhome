"""House-only requests go to the own home model; everything else, and the
house whenever that model's PC doesn't answer, to the main model."""

import asyncio
from unittest.mock import AsyncMock

import httpx

from app.agent import JarvisAgent
from app.db import connect
from app.llm.base import ContentBlock, LLMResponse
from app.memory import MemoryStore
from app.tools.registry import Tool, ToolRegistry


def said(text: str) -> LLMResponse:
    return LLMResponse(content=[ContentBlock(type="text", text=text)], stop_reason="end_turn")


def llm(*responses):
    fake = AsyncMock()
    fake.generate = AsyncMock(side_effect=list(responses))
    return fake


def make(main, home, tmp_path) -> JarvisAgent:
    tools = ToolRegistry()
    for name in ("control_devices", "get_weather"):
        tools.register(Tool(name=name, description="", parameters={"type": "object"},
                            handler=AsyncMock(return_value={"ok": True})))
    return JarvisAgent(llm=main, tools=tools, memory=MemoryStore(connect(str(tmp_path / "t.db"))), home_llm=home)


def test_the_house_goes_to_the_home_model_and_the_rest_to_the_main_one(tmp_path):
    main, home = llm(said("Солнечно."), said("Включила и солнечно.")), llm(said("Выключила."))
    agent = make(main, home, tmp_path)
    assert asyncio.run(agent.chat("s", "r", "Джарвис, выключи свет на кухне"))["local"] is True
    assert asyncio.run(agent.chat("s2", "r", "какая погода"))["local"] is False
    assert asyncio.run(agent.chat("s3", "r", "включи свет и скажи погоду"))["local"] is False  # mixed: main
    assert home.generate.await_count == 1 and main.generate.await_count == 2
    sent = home.generate.await_args.kwargs["tools"]
    assert [t.name for t in sent] == ["control_devices"]


def test_when_the_home_model_is_off_the_main_one_answers_and_it_is_left_alone_a_while(tmp_path):
    home = llm(httpx.ConnectError("refused"))
    main = llm(said("Выключила."), said("Включила."))
    agent = make(main, home, tmp_path)
    first = asyncio.run(agent.chat("s", "r", "выключи свет на кухне"))
    assert first == {"response": "Выключила.", "actions": [], "local": False}
    asyncio.run(agent.chat("s", "r", "включи свет в зале"))
    assert home.generate.await_count == 1  # not tried again right away - no 3 s wait per command
    assert main.generate.await_count == 2


def test_without_a_home_model_nothing_changes(tmp_path):
    main = llm(said("Выключила."))
    agent = make(main, None, tmp_path)
    assert asyncio.run(agent.chat("s", "r", "выключи свет на кухне"))["local"] is False
