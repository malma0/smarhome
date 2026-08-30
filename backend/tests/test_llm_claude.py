"""ClaudeProvider does two translation jobs: our neutral ToolDef -> Anthropic's
input_schema shape on the way out, and Anthropic's SDK content blocks -> our
neutral ContentBlock on the way back. Both are verified here with the real
AsyncAnthropic client's HTTP call mocked out - no network access."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.llm.base import ToolDef
from app.llm.claude import ClaudeProvider


def make_provider() -> ClaudeProvider:
    return ClaudeProvider(api_key="test-key", model="claude-sonnet-5")


def test_tool_schema_translated_to_anthropic_input_schema():
    provider = make_provider()
    tool = ToolDef(name="get_thing", description="does a thing", parameters={"type": "object"})
    provider._client.messages.create = AsyncMock(
        return_value=SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")], stop_reason="end_turn"
        )
    )

    asyncio.run(provider.generate(system="sys", messages=[], tools=[tool]))

    sent_tools = provider._client.messages.create.call_args.kwargs["tools"]
    assert sent_tools == [{"name": "get_thing", "description": "does a thing", "input_schema": {"type": "object"}}]


def test_text_and_tool_use_blocks_normalized():
    provider = make_provider()
    provider._client.messages.create = AsyncMock(
        return_value=SimpleNamespace(
            content=[
                SimpleNamespace(type="text", text="here you go"),
                SimpleNamespace(type="tool_use", id="call_1", name="get_thing", input={"x": 1}),
            ],
            stop_reason="tool_use",
        )
    )

    result = asyncio.run(provider.generate(system="sys", messages=[], tools=[]))

    assert result.stop_reason == "tool_use"
    assert result.content[0].type == "text" and result.content[0].text == "here you go"
    assert result.content[1].type == "tool_use"
    assert result.content[1].id == "call_1"
    assert result.content[1].name == "get_thing"
    assert result.content[1].input == {"x": 1}


def test_stop_sequence_normalized_to_end_turn():
    provider = make_provider()
    provider._client.messages.create = AsyncMock(
        return_value=SimpleNamespace(content=[], stop_reason="stop_sequence")
    )

    result = asyncio.run(provider.generate(system="sys", messages=[], tools=[]))

    assert result.stop_reason == "end_turn"


def test_content_block_to_dict_round_trip():
    from app.llm.base import ContentBlock

    text = ContentBlock(type="text", text="hi")
    assert text.to_dict() == {"type": "text", "text": "hi"}

    tool_use = ContentBlock(type="tool_use", id="call_1", name="get_thing", input={"x": 1})
    assert tool_use.to_dict() == {"type": "tool_use", "id": "call_1", "name": "get_thing", "input": {"x": 1}}
