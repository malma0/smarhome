import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.domains.booking import make_handler, parse
from app.tools import router
from app.tools.registry import TurnContext

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone(timedelta(hours=7)))
FOUND = ('Нашла: {"name": "President", "address": "ул. Ленина, 1", "phone": "+7 383 000-00-00", '
         '"online_booking": true, "system": "yclients", "booking_url": "https://n123.yclients.com/"}')


def _post(text):
    async def post(body):
        return SimpleNamespace(status_code=200, json=lambda: {"choices": [{"message": {"content": text}}]})
    return post


def _run(handler, **tool_input):
    return asyncio.run(handler(tool_input, TurnContext()))


def test_find_then_open_the_booking_page():
    opened = []
    handler = make_handler(_post(FOUND), open_url=opened.append, now=lambda: NOW)
    found = _run(handler, action="find", place="парикмахерская President")
    assert found["system"] == "yclients" and found["online_booking"] is True
    assert found["booked"] is False  # a lookup is not a booking - the model once said "Записала"
    assert _run(handler, action="open")["opened"] == "https://n123.yclients.com/"
    assert opened == ["https://n123.yclients.com/"]


def test_no_online_booking_is_said_with_the_phone():
    text = '{"name": "Салон", "phone": "+7 383 111", "online_booking": false, "system": "none", "booking_url": null}'
    handler = make_handler(_post(text), open_url=lambda u: None, now=lambda: NOW)
    found = _run(handler, action="find", place="салон")
    assert found["online_booking"] is False and found["phone"] == "+7 383 111"
    assert "error" in _run(handler, action="open")


def test_only_web_links_are_opened_and_online_needs_a_link():
    bad = parse('{"name": "X", "online_booking": true, "system": "weird", "booking_url": "javascript:alert(1)"}')
    assert bad["booking_url"] is None and bad["online_booking"] is False and bad["system"] == "none"
    assert parse("ничего не нашла") is None


def test_voice_booking_says_it_is_not_there_yet():
    handler = make_handler(_post(FOUND), open_url=lambda u: None, now=lambda: NOW)
    assert "open" in _run(handler, action="book")["error"]


def test_booking_words_reach_it():
    for text in ("запиши меня в парикмахерскую President", "можно записаться к стоматологу", "хочу в барбершоп"):
        assert "booking" in router.select(text, None), text
