"""Humidifiers, curtains, movement, windows, the guard, electricity, history
and schedules - app.domains.home against a fake Home Assistant."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

from app.danger import alert_text
from app.domains.home import make_handlers, make_more_handlers, summarize
from app.tools import router
from app.tools.registry import TurnContext

TZ = timezone(timedelta(hours=7))
NOW = datetime(2026, 10, 1, 9, 0, tzinfo=TZ)


def _state(entity_id, state, **attrs):
    return {"entity_id": entity_id, "state": state, "attributes": attrs}


class FakeHA:
    def __init__(self):
        self.calls, self.history = [], {}
        self.states = [
            _state("light.bedroom", "off", friendly_name="Спальня: свет"),
            _state("humidifier.bedroom_humidifier", "on", friendly_name="Спальня: увлажнитель", humidity=40,
                   action="humidifying"),
            _state("cover.bedroom_curtains", "open", friendly_name="Спальня: шторы", current_position=50),
            _state("binary_sensor.bedroom_motion", "on", device_class="motion"),
            _state("binary_sensor.bedroom_window", "on", device_class="window"),
            _state("binary_sensor.bedroom_intrusion", "off", device_class="safety"),
            _state("sensor.bedroom_temperature", "21.5", device_class="temperature", unit_of_measurement="°C"),
            _state("input_number.bedroom_humidity_min", "40.0", unit_of_measurement="%", min=30, max=60),
            _state("input_boolean.security_armed", "off", friendly_name="Охрана"),
            _state("input_boolean.bedroom_motion_sim", "on"),  # a knob, not a device
            _state("sensor.house_power", "1054", unit_of_measurement="W"),
            _state("sensor.house_energy_month", "12.4", unit_of_measurement="kWh"),
            _state("automation.schedule_good_morning", "on", friendly_name="Расписание: «Доброе утро» по будням"),
            _state("automation.schedule_sunset_entrance", "on", friendly_name="Расписание: свет в прихожей на закате"),
            _state("automation.danger_leak", "on", friendly_name="Опасность: протечка"),
            _state("input_datetime.schedule_good_morning_time", "07:00:00"),
        ]
        self.areas = {s["entity_id"]: "Спальня" for s in self.states if "bedroom" in s["entity_id"]}
        self.areas.update({"input_boolean.security_armed": None, "sensor.house_power": None,
                           "sensor.house_energy_month": None})

    async def render_template(self, template):
        return json.dumps(list(self.areas.items()))

    async def get_states(self):
        return self.states

    async def call_service(self, domain, service, entity_id=None, data=None):
        self.calls.append((domain, service, entity_id, data))
        return []

    async def get_history(self, entity_id, start, end):
        return self.history.get(entity_id, [])


def _run(handler, **tool_input):
    return asyncio.run(handler(tool_input, TurnContext()))


def test_the_status_shows_what_the_new_devices_say():
    status, *_ = make_handlers(FakeHA())
    rooms = _run(status)["rooms"]
    bedroom = rooms["Спальня"]
    assert bedroom["humidifier"] == "on, keeps 40 %, humidifying now"
    assert bedroom["curtains"] == "open 50%" and bedroom["movement"] == "now" and bedroom["window"] == "open"
    assert bedroom["danger_sensors"] == {"intrusion": "clear"}
    assert bedroom["norm"] == {"humidity_min": "40 %"}
    assert rooms["Весь дом"] == {"security": "off", "power_now": "1054 W", "electricity_this_month": "12.4 kWh"}


def test_curtains_halfway_and_the_guard_wherever_it_is():
    ha = FakeHA()
    _, control, set_norm, _ = make_handlers(ha)
    assert "done" in _run(control, room="спальня", device="curtains", action="on", position=50)
    assert "done" in _run(control, room="где угодно", device="security", action="on")
    assert "done" in _run(set_norm, room="в спальне", humidity_min=45)
    assert ha.calls == [
        ("cover", "set_cover_position", "cover.bedroom_curtains", {"position": 50}),
        ("input_boolean", "turn_on", "input_boolean.security_armed", None),
        ("input_number", "set_value", "input_number.bedroom_humidity_min", {"value": 45.0}),
    ]


def test_last_nights_temperature_min_max_and_a_fair_average():
    ha = FakeHA()
    ha.history["sensor.bedroom_temperature"] = [
        {"state": "22.0", "last_changed": "2026-09-30T16:00:00+00:00"},  # 23:00 local, before the start
        {"state": "20.0", "last_changed": "2026-09-30T19:00:00+00:00"},  # 02:00
        {"state": "unavailable", "last_changed": "2026-09-30T20:00:00+00:00"},
        {"state": "21.0", "last_changed": "2026-09-30T22:00:00+00:00"},  # 05:00
    ]
    history, _ = make_more_handlers(ha, now=lambda: NOW)
    result = _run(history, what="temperature", room="спальне", start="2026-10-01T00:00", end="2026-10-01T07:00")
    assert result["min"] == {"value": 20.0, "at": "2026-10-01 02:00"}
    assert result["max"] == {"value": 22.0, "at": "2026-10-01 00:00"}
    assert result["average"] == 20.9  # 22 for 2 h, 20 for 3 h (the gap keeps the last value), 21 for 2 h


def test_electricity_used_in_a_period():
    ha = FakeHA()
    ha.history["sensor.house_energy"] = [{"state": "100.0", "last_changed": "2026-09-01T00:00:00+07:00"},
                                         {"state": "112.4", "last_changed": "2026-10-01T08:00:00+07:00"}]
    history, _ = make_more_handlers(ha, now=lambda: NOW)
    assert _run(history, what="electricity", start="2026-09-01T00:00")["used_kwh"] == 12.4
    assert "error" in _run(history, what="temperature", room="спальня")  # no records


def test_schedules_listed_moved_and_switched_off():
    ha = FakeHA()
    _, schedule = make_more_handlers(ha, now=lambda: NOW)
    listed = _run(schedule, action="list")["schedules"]
    assert {"name": "«Доброе утро» по будням", "on": True, "time": "07:00"} in listed
    assert all("Опасность" not in s["name"] for s in listed)  # only the house's schedules
    moved = _run(schedule, action="set_time", name="доброе утро", time="7:30")
    assert moved["done"]["time"] == "07:30"
    assert "error" in _run(schedule, action="set_time", name="свет на закате", time="18:00")  # follows the sun
    assert "done" in _run(schedule, action="disable", name="свет в прихожей на закате")
    assert ha.calls == [
        ("input_datetime", "set_datetime", "input_datetime.schedule_good_morning_time", {"time": "07:30:00"}),
        ("automation", "turn_off", "automation.schedule_sunset_entrance", None),
    ]


def test_an_average_holds_a_value_for_as_long_as_it_lasted():
    start = datetime(2026, 10, 1, 0, 0, tzinfo=TZ)
    points = [(start, 10.0), (start + timedelta(hours=9), 20.0)]
    assert summarize(points, start + timedelta(hours=10), TZ)["average"] == 11.0


def test_movement_in_the_guarded_house_is_a_danger_said_out_loud():
    assert alert_text("safety", "Коридор", True, []) == "Внимание! Движение в пустом доме: коридор!"


def test_the_new_words_reach_the_house():
    for text in ("закрой шторы", "поставь на охрану", "сколько электричества за месяц", "открыто ли окно в спальне"):
        assert "home" in router.select(text, None), text


def test_a_question_without_a_time_is_about_now_even_if_the_model_asks_the_history():
    ha = FakeHA()
    history, _ = make_more_handlers(ha, now=lambda: NOW)
    now = asyncio.run(history({"what": "temperature", "room": "спальня"}, TurnContext(said="сколько градусов в спальне")))
    assert now["rooms"]["Спальня"]["temperature"] == "21.5 °C"  # the status, not a min/max of the day
    earlier = asyncio.run(history({"what": "temperature", "room": "спальня"}, TurnContext(said="а ночью сколько было")))
    assert "rooms" not in earlier  # a time named: the history (none recorded here)
    panel = asyncio.run(history({"what": "temperature", "room": "спальня"}, TurnContext()))  # no words: the panel's chart
    assert "rooms" not in panel
