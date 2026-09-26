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


def fake_http_response(payload: dict, status_code: int = 200, headers: dict | None = None) -> object:
    class _Resp:
        def json(self):
            return payload

        def raise_for_status(self):
            if status_code >= 400:
                raise RuntimeError(f"HTTP {status_code}")

    resp = _Resp()
    resp.status_code = status_code
    resp.headers = headers or {}
    return resp


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


def test_temperature_defaults_to_a_conservative_value_and_is_sent():
    provider = make_provider()
    provider._client.post = AsyncMock(
        return_value=fake_http_response(chat_completion({"role": "assistant", "content": "ok"}))
    )

    asyncio.run(provider.generate(system="sys", messages=[], tools=[]))

    sent = provider._client.post.call_args.kwargs["json"]
    assert sent["temperature"] == 0.4


def test_custom_temperature_is_passed_through():
    provider = GroqProvider(api_key="test-key", model="openai/gpt-oss-120b", temperature=0.9)
    provider._client.post = AsyncMock(
        return_value=fake_http_response(chat_completion({"role": "assistant", "content": "ok"}))
    )

    asyncio.run(provider.generate(system="sys", messages=[], tools=[]))

    sent = provider._client.post.call_args.kwargs["json"]
    assert sent["temperature"] == 0.9


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


def test_rate_limit_waits_as_told_and_retries(monkeypatch):
    provider = make_provider()
    waits = []

    async def fake_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr("app.llm.groq.asyncio.sleep", fake_sleep)
    ok = fake_http_response(chat_completion({"role": "assistant", "content": "ok"}))
    provider._client.post = AsyncMock(side_effect=[fake_http_response({}, 429, {"retry-after": "3"}), ok])

    result = asyncio.run(provider.generate(system="sys", messages=[], tools=[]))

    assert result.content[0].text == "ok" and waits == [3.0]


def test_rate_limit_with_a_long_wait_fails_at_once(monkeypatch):
    import pytest

    provider = make_provider()
    monkeypatch.setattr("app.llm.groq.asyncio.sleep", AsyncMock())
    provider._client.post = AsyncMock(return_value=fake_http_response({}, 429, {"retry-after": "600"}))

    with pytest.raises(RuntimeError, match="429"):
        asyncio.run(provider.generate(system="sys", messages=[], tools=[]))
    assert provider._client.post.call_count == 1


def test_rate_limit_gives_up_after_a_few_retries(monkeypatch):
    import pytest

    provider = make_provider()
    monkeypatch.setattr("app.llm.groq.asyncio.sleep", AsyncMock())
    provider._client.post = AsyncMock(return_value=fake_http_response({}, 429, {"retry-after": "1"}))

    with pytest.raises(RuntimeError, match="429"):
        asyncio.run(provider.generate(system="sys", messages=[], tools=[]))
    assert provider._client.post.call_count == 3


def test_a_tool_call_groq_rejected_on_schema_is_recovered():
    """Seen live: get_home_status with room: null against a string schema
    came back as a 400 and the whole turn failed."""
    provider = make_provider()
    error = {"error": {"code": "tool_use_failed", "message": "Tool call validation failed",
                       "failed_generation": '{"name": "get_home_status", "arguments": {"room": null}}'}}
    provider._client.post = AsyncMock(return_value=fake_http_response(error, 400))

    result = asyncio.run(provider.generate(system="sys", messages=[], tools=[]))

    assert result.stop_reason == "tool_use"
    assert result.content[0].name == "get_home_status" and result.content[0].input == {}


def test_other_400s_still_raise():
    import pytest

    provider = make_provider()
    provider._client.post = AsyncMock(return_value=fake_http_response({"error": {"code": "context_length_exceeded"}}, 400))
    with pytest.raises(RuntimeError, match="400"):
        asyncio.run(provider.generate(system="sys", messages=[], tools=[]))


def test_gpt_oss_reasons_briefly():
    provider = make_provider()
    provider._client.post = AsyncMock(
        return_value=fake_http_response(chat_completion({"role": "assistant", "content": "ok"}))
    )
    asyncio.run(provider.generate(system="sys", messages=[], tools=[]))
    assert provider._client.post.call_args.kwargs["json"]["reasoning_effort"] == "low"


def test_an_answer_written_twice_is_kept_once():
    from app.llm.groq import _drop_repeated_answer

    seen_live = [
        ("Самая тёплая комната сейчас — кухня, 25.5 °C.Самая тёплая комната сейчас — кухня, 25,5 °C.",
         "Самая тёплая комната сейчас — кухня, 25,5 °C."),
        ("Свет в спальне установлен на 30 %. Что ещё нужно?Свет в спальне установлен на 30 %. Что ещё могу сделать?",
         "Свет в спальне установлен на 30 %. Что ещё могу сделать?"),
        ("Кондиционер в кабинете включён и установлен на 22 °C.Кондиционер в кабинете включён и установлен на 22 °C.",
         "Кондиционер в кабинете включён и установлен на 22 °C."),
    ]
    for doubled, once in seen_live:
        assert _drop_repeated_answer(doubled) == once
    for normal in ("Свет включён. Что-нибудь ещё?", "Готово.Да, всё выключено.", "Версия 2.0 вышла.", ""):
        assert _drop_repeated_answer(normal) == normal



def test_the_models_leaked_thinking_is_cut_off():
    from app.llm.groq import _drop_leaked_reasoning

    seen_live = [
        ("Поставила ставку?... \n\nOops, need correct.Поставила воспроизведение на паузу.",
         "Поставила воспроизведение на паузу."),
        ("Следующий трент? \n\nНад тихой тих...... \n\nThe assistant should respond properly.Перешла к следующему треку.",
         "Перешла к следующему треку."),
        ("Найдено     \n\n\n\nWe need short answer: opened YouTube search.Открыла поиск на YouTube — «как варить глинтвейн».",
         "Открыла поиск на YouTube — «как варить глинтвейн»."),
    ]
    for leaked, clean in seen_live:
        assert _drop_leaked_reasoning(leaked) == clean
    for fine in ("Открыла Steam.", "Включила Never Gonna Give You Up.", "Громкость 40%.", "",
                 "Сейчас играет The Show Must Go On. Что дальше?"):
        assert _drop_leaked_reasoning(fine) == fine, fine



def _two_models():
    return GroqProvider(api_key="k", model="openai/gpt-oss-120b", fallback_models=("openai/gpt-oss-20b", "qwen/qwen3.8-27b"))


def test_when_one_model_runs_out_the_next_answers_at_once(monkeypatch):
    provider = _two_models()
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("app.llm.groq.asyncio.sleep", fake_sleep)
    day_limit = fake_http_response({"error": {"message": "tokens per day (TPD)"}}, 429, {"retry-after": "541"})
    ok = fake_http_response(chat_completion({"role": "assistant", "content": "ok"}))
    provider._client.post = AsyncMock(side_effect=[day_limit, ok, ok])

    asyncio.run(provider.generate(system="s", messages=[], tools=[]))
    models = [c.kwargs["json"]["model"] for c in provider._client.post.call_args_list]
    assert models == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"] and slept == []
    assert provider.last_model == "openai/gpt-oss-20b"

    asyncio.run(provider.generate(system="s", messages=[], tools=[]))  # the tired one is skipped for now
    assert provider._client.post.call_args.kwargs["json"]["model"] == "openai/gpt-oss-20b"


def test_each_model_gets_its_own_settings():
    provider = _two_models()
    assert provider._body("openai/gpt-oss-20b", [], [])["reasoning_effort"] == "low"
    qwen = provider._body("qwen/qwen3.8-27b", [], [])
    assert qwen["reasoning_format"] == "hidden" and "reasoning_effort" not in qwen


def test_all_models_resting_is_an_error_from_the_soonest(monkeypatch):
    import pytest

    provider = GroqProvider(api_key="k", model="a", fallback_models=("b",))
    monkeypatch.setattr("app.llm.groq.asyncio.sleep", AsyncMock())
    tired = [fake_http_response({}, 429, {"retry-after": "900"}), fake_http_response({}, 429, {"retry-after": "300"})]
    provider._client.post = AsyncMock(side_effect=tired)
    with pytest.raises(RuntimeError, match="429"):
        asyncio.run(provider.generate(system="s", messages=[], tools=[]))
    with pytest.raises(RuntimeError, match="429"):  # nothing is even sent while both rest
        asyncio.run(provider.generate(system="s", messages=[], tools=[]))
    assert provider._client.post.call_count == 2
