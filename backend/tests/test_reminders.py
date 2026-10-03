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


def test_every_weekday_at_8_said_on_a_saturday_morning_starts_on_monday(tmp_path):
    from app.reminders import parse_repeat

    assert parse_repeat("mon,tue,wed,thu,fri") == "weekdays"
    assert parse_repeat("пн, ср") == "mon,wed" and parse_repeat("sat,sun") == "weekends"
    store, handler = _setup(tmp_path)
    saturday = datetime(2026, 9, 26, 7, 30, tzinfo=TZ)  # NOW is a Saturday
    assert saturday.weekday() == 5
    result = _run(handler, kind="reminder", text="таблетки", at="2026-09-26T08:00", repeat="weekdays")
    assert result["added"]["at"] == "2026-09-28 08:00"
    assert result["added"]["repeat"] == "по будням в 08:00"


def test_every_day_at_7_said_after_7_starts_tomorrow_instead_of_failing(tmp_path):
    store, handler = _setup(tmp_path)
    result = _run(handler, kind="reminder", text="зарядка", at="2026-09-26T07:00", repeat="daily")
    assert result["added"]["at"] == "2026-09-27 07:00"


def test_a_repeating_one_rings_then_moves_on_once_even_after_days_off(tmp_path):
    store, handler = _setup(tmp_path)
    _run(handler, kind="reminder", text="полить цветы", at="2026-09-28T09:00", repeat="mon,wed")
    [item] = store.pending()
    assert item["repeat"] == "mon,wed"
    later = datetime(2026, 10, 5, 12, 0, tzinfo=TZ)  # Jarvis was off for a week
    store.rang(item, later)
    [item] = store.pending()
    assert item["due"] == datetime(2026, 10, 7, 9, 0, tzinfo=TZ)  # the next Wednesday, not the missed ones


def test_repeat_words_and_what_is_refused(tmp_path):
    from app.reminders import repeat_words

    assert repeat_words("mon,wed") == "по понедельникам и средам"
    assert repeat_words("fri") == "по пятницам"
    _, handler = _setup(tmp_path)
    assert "error" in _run(handler, kind="timer", in_seconds=60, repeat="daily")
    assert "error" in _run(handler, kind="reminder", text="x", at="2026-09-27T08:00", repeat="иногда")


def test_a_one_off_is_done_when_it_rings(tmp_path):
    store, handler = _setup(tmp_path)
    _run(handler, kind="reminder", text="позвонить маме", at="2026-09-26T09:00")
    [item] = store.pending()
    store.rang(item, NOW)
    assert store.pending() == []


def test_every_ring_is_logged_for_the_phones_and_old_ones_are_dropped(tmp_path):
    store, handler = _setup(tmp_path)
    _run(handler, kind="reminder", text="таблетки", at="2026-09-27T08:00", repeat="daily")
    _run(handler, kind="timer", in_seconds=60)
    reminder, timer = sorted(store.pending(), key=lambda r: r["kind"])  # "reminder" < "timer"
    store.rang(timer, NOW)
    store.rang(reminder, NOW + timedelta(days=1))
    rings = store.rings_since(NOW - timedelta(minutes=1))
    assert [(r["kind"], r["text"]) for r in rings] == [("timer", timer["text"]), ("reminder", "таблетки")]
    assert [r["text"] for r in store.rings_since(NOW)] == ["таблетки"]  # only after the moment asked
    store.rang(store.pending()[0], NOW + timedelta(days=4))  # two days on: the old rings are gone
    assert [r["at"][:10] for r in store.rings_since(NOW - timedelta(days=9))] == ["2026-09-30"]
    # a second connection (the panel's thread) sees them
    assert len(ReminderStore(connect(str(tmp_path / "j.db"))).rings_since(NOW - timedelta(days=9))) == 1


def test_a_reminder_is_whoever_set_it_or_for_someone_else_by_name(tmp_path):
    from app.reminders import match_resident

    store = ReminderStore(connect(str(tmp_path / "j.db")))
    handler = make_handler(store, now=lambda: NOW, residents=lambda: ["default", "Матвей", "Эля"])
    mine = asyncio.run(handler({"kind": "timer", "in_seconds": 60}, TurnContext(resident="Матвей")))["added"]
    hers = asyncio.run(handler({"kind": "reminder", "text": "купить хлеб", "in_seconds": 600, "for": "Эле"},
                               TurnContext(resident="Матвей")))["added"]
    nobodys = asyncio.run(handler({"kind": "timer", "in_seconds": 90}, TurnContext()))["added"]
    assert mine["for"] == "Матвей" and hers["for"] == "Эля" and "for" not in nobodys
    assert "error" in asyncio.run(handler({"kind": "timer", "in_seconds": 60, "for": "Пете"}, TurnContext()))
    assert match_resident("матвею", ["Матвей", "Эля"]) == "Матвей" and match_resident("Эля", ["Матвей", "Эля"]) == "Эля"
    assert match_resident("ма", ["Матвей", "Маша"]) is None


def test_stop_on_a_phone_marks_that_ring_and_only_that_one(tmp_path):
    store, handler = _setup(tmp_path)
    _run(handler, kind="timer", in_seconds=60)
    _run(handler, kind="timer", in_seconds=90)
    first, second = store.pending()
    ring1, ring2 = store.rang(first, NOW), store.rang(second, NOW)
    assert store.stopped_rings([ring1, ring2]) == set()
    assert store.stop_ring(ring2) and not store.stop_ring(999)
    assert store.stopped_rings([ring1, ring2]) == {ring2} and store.stopped_rings([]) == set()
    assert [r["stopped"] for r in store.rings_since(NOW - timedelta(minutes=1))] == [False, True]


def test_for_everyone_is_no_ones_and_a_timer_knows_when_it_started(tmp_path):
    from app.reminders import public

    store = ReminderStore(connect(str(tmp_path / "j.db")))
    handler = make_handler(store, now=lambda: NOW, residents=lambda: ["Матвей"])
    added = asyncio.run(handler({"kind": "reminder", "text": "мусор", "in_seconds": 600, "for": "Всем"},
                                TurnContext(resident="Матвей")))["added"]
    assert "for" not in added and store.pending()[0]["resident_id"] is None
    assert public(store.pending()[0], NOW)["created"]
