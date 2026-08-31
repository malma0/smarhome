"""OllamaProvider does more translation than ClaudeProvider, because our
wire format (llm/base.py) happens to be Anthropic-shaped: assistant
tool_use/tool_result history has to be converted to Ollama's OpenAI-style
messages on the way out, and its tool_calls parsed back into our normalized
ContentBlock on the way in. All verified here with the HTTP call mocked -
no real Ollama server needed."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.llm.base import ContentBlock, ToolDef
from app.llm.ollama import OllamaProvider


def make_provider() -> OllamaProvider:
    return OllamaProvider(model="qwen2.5:1.5b")


def fake_http_response(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(json=lambda: payload, raise_for_status=lambda: None)


def test_tool_schema_translated_to_openai_style_function():
    provider = make_provider()
    tool = ToolDef(name="get_thing", description="does a thing", parameters={"type": "object"})
    provider._client.post = AsyncMock(
        return_value=fake_http_response({"message": {"content": "ok"}, "done_reason": "stop"})
    )

    asyncio.run(provider.generate(system="sys", messages=[], tools=[tool]))

    sent = provider._client.post.call_args.kwargs["json"]
    assert sent["tools"] == [
        {"type": "function", "function": {"name": "get_thing", "description": "does a thing", "parameters": {"type": "object"}}}
    ]


def test_system_prompt_becomes_system_message():
    provider = make_provider()
    provider._client.post = AsyncMock(
        return_value=fake_http_response({"message": {"content": "ok"}, "done_reason": "stop"})
    )

    asyncio.run(provider.generate(system="be helpful", messages=[], tools=[]))

    sent_messages = provider._client.post.call_args.kwargs["json"]["messages"]
    assert sent_messages[0] == {"role": "system", "content": "be helpful"}


def test_plain_text_response_parsed_as_end_turn():
    provider = make_provider()
    provider._client.post = AsyncMock(
        return_value=fake_http_response({"message": {"content": "Paris."}, "done_reason": "stop"})
    )

    result = asyncio.run(provider.generate(system="sys", messages=[], tools=[]))

    assert result.stop_reason == "end_turn"
    assert result.content == [ContentBlock(type="text", text="Paris.")]


def test_tool_call_response_parsed_as_tool_use():
    provider = make_provider()
    provider._client.post = AsyncMock(
        return_value=fake_http_response(
            {
                "message": {
                    "content": "",
                    "tool_calls": [{"id": "call_1", "function": {"name": "get_thing", "arguments": {"x": 1}}}],
                },
                "done_reason": "stop",
            }
        )
    )

    result = asyncio.run(provider.generate(system="sys", messages=[], tools=[]))

    assert result.stop_reason == "tool_use"
    assert len(result.content) == 1
    block = result.content[0]
    assert block.type == "tool_use"
    assert block.id == "call_1"
    assert block.name == "get_thing"
    assert block.input == {"x": 1}


def test_missing_tool_call_id_is_synthesized():
    provider = make_provider()
    provider._client.post = AsyncMock(
        return_value=fake_http_response(
            {
                "message": {
                    "content": "",
                    "tool_calls": [{"function": {"name": "get_thing", "arguments": {}}}],
                },
                "done_reason": "stop",
            }
        )
    )

    result = asyncio.run(provider.generate(system="sys", messages=[], tools=[]))

    assert result.content[0].id  # non-empty, synthesized


def test_assistant_history_with_tool_use_translated_to_tool_calls():
    provider = make_provider()
    provider._client.post = AsyncMock(
        return_value=fake_http_response({"message": {"content": "done"}, "done_reason": "stop"})
    )
    history = [
        {"role": "user", "content": "turn on the light"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "call_1", "name": "set_light", "input": {"on": True}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "{'ok': True}", "is_error": False}],
        },
    ]

    asyncio.run(provider.generate(system="sys", messages=history, tools=[]))

    sent_messages = provider._client.post.call_args.kwargs["json"]["messages"]
    assert sent_messages[1] == {"role": "user", "content": "turn on the light"}
    assert sent_messages[2] == {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "call_1", "function": {"name": "set_light", "arguments": {"on": True}}}],
    }
    assert sent_messages[3] == {"role": "tool", "content": "{'ok': True}", "tool_call_id": "call_1"}
