import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.domains.web_answer import clean, make_handler
from app.tools import router
from app.tools.registry import TurnContext

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone(timedelta(hours=7)))


def _reply(status, content=""):
    return SimpleNamespace(status_code=status, json=lambda: {"choices": [{"message": {"content": content}}]})


def _run(handler, **tool_input):
    return asyncio.run(handler(tool_input, TurnContext()))


def test_an_answer_comes_back_clean_for_reading_out():
    sent = []
    async def post(body):
        sent.append(body)
        return _reply(200, "**Курс биткоина** — $78 917 【1†L32-L38】.")
    result = _run(make_handler(post, now=lambda: NOW), question="сколько стоит биткоин")
    assert result == {"answer": "Курс биткоина — $78 917."}
    assert sent[0]["model"] == "openai/gpt-oss-20b" and sent[0]["tools"] == [{"type": "browser_search"}]
    assert "2026-09-30" in sent[0]["messages"][0]["content"]  # it guessed "29 августа" without the date


def test_out_of_limit_goes_to_the_next_model_then_says_so():
    models = []
    async def post(body):
        models.append(body["model"])
        return _reply(429)
    result = _run(make_handler(post, now=lambda: NOW), question="новости")
    assert models == ["openai/gpt-oss-20b", "openai/gpt-oss-120b"] and "limit" in result["error"]


def test_marks_and_markdown_go():
    assert clean("## Новости\n- **Раз** 【2†L1】\n- Два") == "Новости\nРаз\nДва"


def test_questions_for_the_web_reach_it():
    for text in ("какие новости сегодня", "кто выиграл вчера матч", "сколько стоит айфон 17", "узнай курс биткоина"):
        assert "web" in router.select(text, None), text
