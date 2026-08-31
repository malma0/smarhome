"""GroqProvider speaks the standard OpenAI chat-completions wire format -
tool call arguments are a JSON string, tool_calls wrapped in a
"type": "function" envelope. All verified with the HTTP call mocked, no
real Groq API access needed."""

import asyncio
from unittest.mock import AsyncMock

from app.llm.base import ContentBlock, ToolDef
from app.llm.groq import GroqProvider


def make_provider() -> GroqProvider:
    return GroqProvider(api_key="test-key", model="openai/gpt-oss-120b")


def fake_http_response(payload: dict) -> object:
    class _Resp:
        def json(self):
            return payload

        def raise_for_status(self):
            pass

    return _Resp()


def chat_completion(message: dict, finish_reason: str = "stop") -> dict:
    return {"choices": [{"message": message, "finish_reason": finish_reason}]}


def test_tool_schema_translated_to_openai_function_envelope():
    provider = make_provider()
    tool = ToolDef(name="get_thing", description="does a thing", parameters={"type": "object"})
    provider._client.post = AsyncMock(
        return_value=fake_http_response(chat_completion({"role": "assistant", "content": "ok"}))
    )

    asyncio.run(provider.generate(system="sys", messages=[], tools=[tool]))

    sent = provider._client.post.call_args.kwargs["json"]
    assert sent["tools"] == [
        {
            "type": "function",
            "function": {"name": "get_thing", "description": "does a thing", "parameters": {"type": "object"}},
        }
    ]


def test_system_prompt_becomes_system_message():
    provider = make_provider()
    provider._client.post = AsyncMock(
        return_value=fake_http_response(chat_completion({"role": "assistant", "content": "ok"}))
    )

    asyncio.run(provider.generate(system="be helpful", messages=[], tools=[]))

    sent_messages = provider._client.post.call_args.kwargs["json"]["messages"]
    assert sent_messages[0] == {"role": "system", "content": "be helpful"}


def test_plain_text_response_parsed_as_end_turn():
    provider = make_provider()
    provider._client.post = AsyncMock(
        return_value=fake_http_response(chat_completion({"role": "assistant", "content": "Paris"}))
    )

    result = asyncio.run(provider.generate(system="sys", messages=[], tools=[]))

    assert result.stop_reason == "end_turn"
    assert result.content == [ContentBlock(type="text", text="Paris")]


def test_tool_call_arguments_are_json_decoded():
    provider = make_provider()
    provider._client.post = AsyncMock(
        return_value=fake_http_response(
            chat_completion(
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "fc_1",
                            "type": "function",
                            "function": {"name": "get_thing", "arguments": '{"x": 1}'},
                        }
                    ],
                },
                finish_reason="tool_calls",
            )
        )
    )

    result = asyncio.run(provider.generate(system="sys", messages=[], tools=[]))

    assert result.stop_reason == "tool_use"
    block = result.content[0]
    assert block.type == "tool_use"
    assert block.id == "fc_1"
    assert block.name == "get_thing"
    assert block.input == {"x": 1}


def test_length_finish_reason_mapped_to_max_tokens():
    provider = make_provider()
    provider._client.post = AsyncMock(
        return_value=fake_http_response(
            chat_completion({"role": "assistant", "content": "cut off..."}, finish_reason="length")
        )
    )

    result = asyncio.run(provider.generate(system="sys", messages=[], tools=[]))

    assert result.stop_reason == "max_tokens"


def test_assistant_history_with_tool_use_translated_with_json_string_arguments():
    provider = make_provider()
    provider._client.post = AsyncMock(
        return_value=fake_http_response(chat_completion({"role": "assistant", "content": "done"}))
    )
    history = [
        {"role": "user", "content": "turn on the light"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "fc_1", "name": "set_light", "input": {"on": True}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "fc_1", "content": "{'ok': True}", "is_error": False}],
        },
    ]

    asyncio.run(provider.generate(system="sys", messages=history, tools=[]))

    sent_messages = provider._client.post.call_args.kwargs["json"]["messages"]
    assert sent_messages[1] == {"role": "user", "content": "turn on the light"}
    assert sent_messages[2] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "fc_1", "type": "function", "function": {"name": "set_light", "arguments": '{"on": true}'}}
        ],
    }
    assert sent_messages[3] == {"role": "tool", "content": "{'ok': True}", "tool_call_id": "fc_1"}
