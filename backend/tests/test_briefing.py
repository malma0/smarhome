""""Что у меня сегодня?" - the weather, this resident's reminders for the day and everyone's,
the timers running, the shopping list; and tomorrow."""

import asyncio
from datetime import datetime, timedelta, timezone

from app import briefing
from app.db import connect
from app.reminders import ReminderStore
from app.shopping import ShoppingList
from app.tools import router
from app.tools.registry import TurnContext

TZ = timezone(timedelta(hours=7))
NOW = datetime(2026, 10, 3, 9, 0, tzinfo=TZ)


async def weather(tool_input, ctx):
    days = [{"date": "2026-10-03", "weather": "ясно", "temperature": "8..15 °C"},
            {"date": "2026-10-04", "weather": "дождь", "temperature": "6..10 °C"}][:tool_input["days"]]
    return {"now": {"temperature": "9 °C"}, "days": days}


def _setup(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    store, shopping = ReminderStore(conn), ShoppingList(conn)
    at = lambda h, m=0, d=0: NOW.replace(hour=h, minute=m) + timedelta(days=d)  # noqa: E731
    store.add("reminder", "таблетки", at(20), resident_id="Матвей", repeat="daily")
    store.add("reminder", "купить хлеб", at(18), resident_id="Эля")
    store.add("reminder", "вынести мусор", at(19))  # no one's in particular
    store.add("reminder", "уже было", at(8))  # earlier today - over
    store.add("reminder", "к врачу", at(10, 30, d=1), resident_id="Матвей")
    store.add("timer", "Таймер на 10 минут", NOW + timedelta(minutes=10), resident_id="Матвей")
    shopping.add("молоко")
    return briefing.make_handler(store, shopping, weather, now=lambda: NOW)


def _run(handler, resident, **tool_input):
    return asyncio.run(handler(tool_input, TurnContext(resident=resident)))


def test_today_is_mine_and_everyones_from_now_on(tmp_path):
    day = _run(_setup(tmp_path), "Матвей")
    assert [(r["at"], r["text"]) for r in day["reminders"]] == [("19:00", "вынести мусор"), ("20:00", "таблетки")]
    assert day["reminders"][1]["repeat"] == "каждый день"
    assert day["timers"] == [{"text": "Таймер на 10 минут", "left": "10 минут"}]
    assert day["weather"]["weather"] == "ясно" and day["weather_now"] == {"temperature": "9 °C"}
    assert day["shopping_list"] == ["молоко"]


def test_tomorrow_and_someone_elses_day(tmp_path):
    handler = _setup(tmp_path)
    tomorrow = _run(handler, "Матвей", day="tomorrow")
    assert [r["text"] for r in tomorrow["reminders"]] == ["к врачу"] and tomorrow["timers"] == []
    assert tomorrow["weather"]["weather"] == "дождь" and "weather_now" not in tomorrow
    assert [r["text"] for r in _run(handler, "Эля")["reminders"]] == ["купить хлеб", "вынести мусор"]
    # the panel or a voice it didn't know: the whole house's day
    assert len(_run(handler, "default")["reminders"]) == 3  # хлеб, мусор, таблетки - "уже было" is over


def test_the_words_that_ask_for_the_day():
    for said in ("Джарвис, что у меня сегодня?", "какие планы на завтра", "что на сегодня"):
        assert "day" in router.select(said, None), said
