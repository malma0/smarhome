import asyncio

from app.domains.currency import make_handler
from app.tools import router
from app.tools.registry import TurnContext

FEED = {"Date": "2026-09-26T11:30:00+03:00", "Valute": {
    "USD": {"Name": "Доллар США", "Nominal": 1, "Value": 84.3414, "Previous": 84.9057},
    "KZT": {"Name": "Тенге", "Nominal": 100, "Value": 19.0865, "Previous": 19.0521},
}}


def _run(handler, **tool_input):
    return asyncio.run(handler(tool_input, TurnContext()))


def test_rates_are_per_one_unit_with_the_days_change():
    async def fetch():
        return FEED
    result = _run(make_handler(fetch), currencies=["usd", "KZT", "XXX"])
    assert result["date"] == "2026-09-26"
    assert result["rates"]["USD"] == {"name": "Доллар США", "rub": 84.3414, "change_since_previous": -0.5643}
    assert result["rates"]["KZT"]["rub"] == 0.1909  # 19.0865 for 100 tenge
    assert result["unknown_codes"] == ["XXX"]


def test_the_feed_is_asked_once_an_hour():
    calls = []
    now = [0.0]
    async def fetch():
        calls.append(1)
        return FEED
    handler = make_handler(fetch, clock=lambda: now[0])
    _run(handler, currencies=["USD"])
    now[0] = 1800
    _run(handler, currencies=["USD"])
    now[0] = 3700
    _run(handler, currencies=["USD"])
    assert len(calls) == 2


def test_a_failed_feed_is_an_answer():
    async def fetch():
        raise OSError("down")
    assert "error" in _run(make_handler(fetch), currencies=["USD"])


def test_money_words_reach_the_rates():
    for text in ("сколько стоит доллар", "курс евро", "100 юаней в рублях"):
        assert "currency" in router.select(text, None), text


def test_words_that_only_look_like_money_stay_out():
    assert "currency" not in (router.select("открой клиент телеграма", None) or set())
