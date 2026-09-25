import asyncio
from datetime import datetime, timedelta, timezone

from app.db import connect
from app.reminders import ReminderStore, human_duration, make_handler, timer_duration_words
from app.tools.registry import TurnContext

TZ = timezone(timedelta(hours=7))
NOW = datetime(2026, 9, 26, 7, 30, tzinfo=TZ)


def _setup(tmp_path):
    store = ReminderStore(connect(str(tmp_path / "j.db")))
    return store, make_handler(store, now=lambda: NOW)


def _run(handler, **tool_input):
    return asyncio.run(handler(tool_input, TurnContext()))


def test_durations_read_as_russian():
    assert human_duration(600) == "10 минут"
    assert human_duration(125) == "2 минуты 5 секунд"
    assert human_duration(5400) == "1 час 30 минут"
    assert human_duration(90061) == "1 день 1 час"
    assert timer_duration_words(60) == "1 минуту"
    assert timer_duration_words(21 * 60) == "21 минуту"


def test_a_timer_gets_its_length_as_its_name(tmp_path):
    store, handler = _setup(tmp_path)
    result = _run(handler, kind="timer", in_seconds=600, text="пельмени")
    assert result["added"] == {"id": 1, "kind": "timer", "text": "Таймер на 10 минут: пельмени",
                               "at": "2026-09-26 07:40", "in": "10 минут"}
    assert [r["text"] for r in store.pending()] == ["Таймер на 10 минут: пельмени"]


def test_a_reminder_at_a_local_time(tmp_path):
    store, handler = _setup(tmp_path)
    result = _run(handler, kind="reminder", text="выключить духовку", at="2026-09-26T08:00")
    assert result["added"]["at"] == "2026-09-26 08:00" and result["added"]["in"] == "30 минут"


def test_what_is_refused(tmp_path):
    store, handler = _setup(tmp_path)
    assert "already passed" in _run(handler, kind="reminder", text="x", at="2026-09-26T07:00")["error"]
    assert "exactly one" in _run(handler, kind="timer")["error"]
    assert "exactly one" in _run(handler, kind="timer", in_seconds=5, at="2026-09-26T08:00")["error"]
    assert "needs its text" in _run(handler, kind="reminder", in_seconds=60)["error"]
    assert "Can't read" in _run(handler, kind="reminder", text="x", at="завтра")["error"]
    assert "a year" in _run(handler, kind="timer", in_seconds=400 * 86400)["error"]
    assert store.pending() == []


def test_due_ones_come_out_once_marked_done(tmp_path):
    store, handler = _setup(tmp_path)
    _run(handler, kind="timer", in_seconds=60)
    _run(handler, kind="reminder", text="позвонить маме", in_seconds=3600)
    due = store.due(NOW + timedelta(minutes=2))
    assert [r["text"] for r in due] == ["Таймер на 1 минуту"]
    store.mark_done(due[0]["id"])
    assert [r["text"] for r in store.pending()] == ["позвонить маме"]


def test_list_and_cancel(tmp_path):
    store, handler = _setup(tmp_path)
    _run(handler, kind="timer", in_seconds=600, text="пельмени")
    _run(handler, kind="reminder", text="позвонить маме", in_seconds=3600)
    assert len(_run(handler, action="list")["pending"]) == 2
    assert _run(handler, action="cancel", text="маме")["cancelled"][0]["text"] == "позвонить маме"
    assert "Nothing matched" in _run(handler, action="cancel", text="собака")["error"]
    assert "Nothing matched" in _run(handler, action="cancel")["error"]  # no "all" by accident
    assert _run(handler, action="cancel", kind="timer")["cancelled"][0]["kind"] == "timer"
    assert store.pending() == []


def test_the_store_survives_a_new_connection(tmp_path):
    store, handler = _setup(tmp_path)
    _run(handler, kind="timer", in_seconds=600)
    again = ReminderStore(connect(str(tmp_path / "j.db")))
    assert len(again.pending()) == 1
