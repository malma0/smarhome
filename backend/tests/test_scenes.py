"""Scenarios and schedules made in the app: steps become the same Home Assistant calls a
command makes, unsafe steps are refused, and the panel creates, lists and deletes them."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app import scenes
from app.panel import create_app
from tests.test_home_features import FakeHA, _state

PIN = "test-pin-1234"


def _build(steps, name="Кино", phrases=("кино", "включи кино")):
    return asyncio.run(scenes.build(FakeHA(), name, list(phrases), steps))


def test_steps_become_home_assistant_calls():
    config, does = _build([
        {"room": "спальня", "device": "light", "action": "on", "brightness_pct": 30},
        {"room": "Спальня", "device": "curtains", "action": "on", "position": 0},
        {"room": "Спальня", "norm": "humidity_min", "value": 45},
        {"room": "где угодно", "device": "security", "action": "on"},
    ])
    assert config["alias"] == "Кино" and config["description"].startswith("Фразы: кино, включи кино. ")
    assert config["sequence"] == [
        {"action": "light.turn_on", "target": {"entity_id": ["light.bedroom"]}, "data": {"brightness_pct": 30}},
        {"action": "cover.set_cover_position", "target": {"entity_id": ["cover.bedroom_curtains"]}, "data": {"position": 0}},
        {"action": "input_number.set_value", "target": {"entity_id": "input_number.bedroom_humidity_min"}, "data": {"value": 45.0}},
        {"action": "input_boolean.turn_on", "target": {"entity_id": ["input_boolean.security_armed"]}},
    ]
    assert does == "Включает свет в спальне на 30%, открывает шторы в спальне, влажность от 45% в спальне, ставит охрану."
    assert config["variables"]["jarvis_steps"][0]["brightness_pct"] == 30  # the app reads them back


def test_what_a_scenario_may_not_do():
    for steps, why in (([], "шаг"), ([{"room": "Спальня", "device": "water_valve", "action": "on"}], "краны"),
                       ([{"room": "Гараж", "device": "light", "action": "off"}], "Гараж"),
                       ([{"room": "Спальня", "device": "ac", "action": "on"}], "кондиционер"),
                       ([{"room": "Спальня", "norm": "humidity_min", "value": 90}], "норма")):
        with pytest.raises(ValueError, match=why):
            _build(steps)
    with pytest.raises(ValueError, match="название"):
        _build([{"room": "Спальня", "device": "light", "action": "off"}], name=" ")


def test_ids_are_latin_and_unique():
    assert scenes.object_id("Кино вечером!", set()) == "kino_vecherom"
    assert scenes.object_id("Кино", {"kino", "kino_2"}) == "kino_3"


def test_a_schedule_runs_a_scenario_at_a_time_on_its_days():
    config = scenes.schedule_config("Кино по пятницам", "21:5", [5, 5, 9], "kino")
    assert config["triggers"] == [{"trigger": "time", "at": "21:05:00"}]
    assert "[5]" in config["conditions"][0]["value_template"]
    assert config["actions"][0]["target"] == {"entity_id": "script.kino"}
    for bad in (("", "21:00", [5], "kino"), ("x", "25:00", [5], "kino"), ("x", "21:00", [], "kino"), ("x", "21:00", [5], "../x")):
        with pytest.raises(ValueError):
            scenes.schedule_config(*bad)


class Made(FakeHA):
    """A house that keeps the scripts and automations the panel writes."""

    def __init__(self):
        super().__init__()
        self.scripts, self.automations = {}, {}

    async def get_script_config(self, object_id):
        return self.scripts.get(object_id, {})

    async def save_script_config(self, object_id, config):
        self.scripts[object_id] = config
        self.states.append(_state(f"script.{object_id}", "off", friendly_name=config["alias"]))

    async def delete_script_config(self, object_id):
        del self.scripts[object_id]
        self.states = [s for s in self.states if s["entity_id"] != f"script.{object_id}"]

    async def get_automation_config(self, automation_id):
        return self.automations[automation_id]

    async def save_automation_config(self, automation_id, config):
        fresh = automation_id not in self.automations
        self.automations[automation_id] = config
        if fresh:
            self.states.append(_state(f"automation.{automation_id}", "on", friendly_name=config["alias"], id=automation_id))

    async def delete_automation_config(self, automation_id):
        del self.automations[automation_id]
        self.states = [s for s in self.states if s.get("attributes", {}).get("id") != automation_id]


def test_the_panel_makes_lists_and_deletes_them():
    ha = Made()
    client = TestClient(create_app(client=ha, pin=PIN))
    h = {"X-Pin": PIN}
    got = client.post("/api/scenes", headers=h, json={"name": "Кино", "phrases": ["кино"], "steps": [
        {"room": "Спальня", "device": "light", "action": "off"}]}).json()
    assert got["id"] == "kino" and got["scenes"][0]["steps"][0]["device"] == "light"
    assert "error" in client.post("/api/scenes", headers=h, json={"name": "кино", "steps": [
        {"room": "Спальня", "device": "light", "action": "on"}]}).json()  # the same name again

    made = client.post("/api/schedules", headers=h, json={"action": "create", "name": "Кино по пятницам",
                                                           "time": "21:00", "days": [5], "scene": "kino"}).json()
    mine = [s for s in made["schedules"] if s.get("app")]
    assert mine == [{"id": "app_kino_po_piatnitsam", "name": "Кино по пятницам", "on": True, "time": "21:00",
                     "days": [5], "days_editable": True, "app": True, "scene": "kino"}]
    moved = client.post("/api/schedules", headers=h, json={"action": "set_time", "id": mine[0]["id"], "time": "22:30"}).json()
    assert [s["time"] for s in moved["schedules"] if s.get("app")] == ["22:30"]
    assert "error" in client.post("/api/schedules", headers=h, json={"action": "delete", "id": "schedule_good_morning"}).json()
    gone = client.post("/api/schedules", headers=h, json={"action": "delete", "id": mine[0]["id"]}).json()
    assert not [s for s in gone["schedules"] if s.get("app")]
    assert client.delete("/api/scenes/kino", headers=h).json()["scenes"] == []
