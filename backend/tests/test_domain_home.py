"""app.domains.home against a fake Home Assistant - no real one needed."""

import asyncio
import json

import pytest

from app.domains.home import match_room, make_handlers, register
from app.ha_client import HomeAssistantError
from app.tools.registry import ToolRegistry, TurnContext

ROOMS = ["Прихожая", "Спальня", "Кабинет", "Зал", "Коридор", "Кухня"]


def _state(entity_id, state, **attrs):
    return {"entity_id": entity_id, "state": state, "attributes": attrs}


class FakeHA:
    def __init__(self, down=False):
        self.down = down
        self.calls = []
        self.states = [
            _state("light.kitchen", "off", friendly_name="Кухня: свет"),
            _state("switch.kitchen_socket", "off", friendly_name="Кухня: розетка"),
            _state("climate.kitchen_ac", "off", friendly_name="Кухня: кондиционер", temperature=24,
                   current_temperature=25.5, hvac_action="off", min_temp=5, max_temp=35),
            _state("sensor.kitchen_temperature", "25.5", friendly_name="Кухня: температура",
                   device_class="temperature", unit_of_measurement="°C"),
            _state("light.bedroom", "on", friendly_name="Спальня: свет", brightness=128),
            _state("switch.bedroom_socket", "off", friendly_name="Спальня: розетка"),
            _state("climate.bedroom_ac", "off", friendly_name="Спальня: кондиционер", temperature=24),
            _state("light.corridor", "off", friendly_name="Коридор: свет"),
            _state("sensor.sun_next_dawn", "2026-09-26T00:00:00", friendly_name="Next dawn"),
            _state("input_boolean.kitchen_light_power", "off"),
        ]
        self.areas = {
            "light.kitchen": "Кухня", "switch.kitchen_socket": "Кухня", "climate.kitchen_ac": "Кухня",
            "sensor.kitchen_temperature": "Кухня", "light.bedroom": "Спальня",
            "switch.bedroom_socket": "Спальня", "climate.bedroom_ac": "Спальня",
            "light.corridor": "Коридор", "sensor.sun_next_dawn": None,
        }

    def _check(self):
        if self.down:
            raise HomeAssistantError("ConnectError")

    async def render_template(self, template):
        self._check()
        return json.dumps(list(self.areas.items()))

    async def get_states(self):
        self._check()
        return self.states

    async def call_service(self, domain, service, entity_id=None, data=None):
        self._check()
        self.calls.append((domain, service, entity_id, data))
        return []


def _run(handler, **tool_input):
    ctx = tool_input.pop("ctx", None) or TurnContext()
    return asyncio.run(handler(tool_input, ctx))


@pytest.mark.parametrize(
    "asked, room",
    [("Кухня", "Кухня"), ("кухне", "Кухня"), ("на кухне", "Кухня"), ("в зале", "Зал"),
     ("прихожей", "Прихожая"), ("спальне", "Спальня"), ("кабинете", "Кабинет"), ("коридоре", "Коридор"),
     ("ванная", None), ("кладовка", None)],
)
def test_rooms_match_in_any_grammatical_form(asked, room):
    assert match_room(asked, ROOMS) == room


def test_status_groups_the_house_by_room_compactly_without_helpers_or_ha_sensors():
    status, _ = make_handlers(FakeHA())
    rooms = _run(status)["rooms"]
    assert set(rooms) == {"Кухня", "Спальня", "Коридор"}  # the sun sensor has no room
    assert rooms["Кухня"] == {"light": "off", "socket": "off", "ac": "off", "temperature": "25.5 °C"}
    assert rooms["Спальня"]["light"] == "on 50%"


def test_an_ac_that_cools_shows_its_target_and_two_lamps_show_both():
    ha = FakeHA()
    ha.states[2] = _state("climate.kitchen_ac", "cool", temperature=22)
    ha.states.append(_state("light.kitchen_2", "on", brightness=255))
    ha.areas["light.kitchen_2"] = "Кухня"
    status, _ = make_handlers(ha)
    kitchen = _run(status, room="кухня")["rooms"]["Кухня"]
    assert kitchen["ac"] == "cool to 22 °C" and kitchen["light"] == ["off", "on 100%"]


def test_status_for_one_room_and_an_unknown_one():
    status, _ = make_handlers(FakeHA())
    assert list(_run(status, room="на кухне")["rooms"]) == ["Кухня"]
    result = _run(status, room="ванная")
    assert "error" in result and "Кухня" in result["rooms"]


def test_light_on_with_brightness():
    ha = FakeHA()
    _, control = make_handlers(ha)
    result = _run(control, room="кухня", device="light", action="on", brightness_pct=40)
    assert result["done"] == [{"room": "Кухня", "device": "light", "action": "on"}]
    assert ha.calls == [("light", "turn_on", "light.kitchen", {"brightness_pct": 40})]


def test_lights_everywhere_need_no_confirmation():
    ha = FakeHA()
    _, control = make_handlers(ha)
    result = _run(control, room="везде", device="light", action="off")
    assert {d["room"] for d in result["done"]} == {"Кухня", "Спальня", "Коридор"}


def test_ac_cools_to_a_temperature():
    ha = FakeHA()
    _, control = make_handlers(ha)
    _run(control, room="кухня", device="ac", action="on", temperature=22)
    assert ha.calls == [("climate", "set_temperature", "climate.kitchen_ac", {"hvac_mode": "cool", "temperature": 22.0})]


@pytest.mark.parametrize("temperature, confirmed, allowed", [
    (16, False, True), (28, False, True), (15, False, False), (30, False, False),
    (30, True, True), (5, True, True), (36, True, False), (4, True, False),
])
def test_ac_temperature_limits(temperature, confirmed, allowed):
    ha = FakeHA()
    _, control = make_handlers(ha)
    result = _run(control, room="кухня", device="ac", action="on", temperature=temperature, confirmed=confirmed)
    assert ("done" in result) == allowed
    assert bool(ha.calls) == allowed


def test_a_second_room_of_acs_or_sockets_in_one_turn_needs_confirmation():
    ha = FakeHA()
    _, control = make_handlers(ha)
    ctx = TurnContext()
    assert "done" in _run(control, room="кухня", device="ac", action="on", ctx=ctx)
    second = _run(control, room="спальня", device="ac", action="on", ctx=ctx)
    assert "confirm" in second["error"] and second["already_changed_this_turn"] == ["Кухня"]
    assert "done" in _run(control, room="спальня", device="ac", action="on", confirmed=True, ctx=ctx)
    assert "error" in _run(control, room="all", device="socket", action="on")
    assert "done" in _run(control, room="кухня", device="ac", action="off", ctx=ctx)  # same room again: fine


def test_a_missing_device_or_room_is_reported_not_guessed():
    ha = FakeHA()
    _, control = make_handlers(ha)
    result = _run(control, room="коридор", device="socket", action="on")
    assert "no socket" in result["error"] and result["rooms_with_it"] == ["Кухня", "Спальня"]
    assert "error" in _run(control, room="ванная", device="light", action="on")
    assert "error" in _run(control, room="кухня", device="tv", action="on")
    assert ha.calls == []


def test_home_assistant_down_is_an_error_result_not_a_crash():
    status, control = make_handlers(FakeHA(down=True))
    assert "unreachable" in _run(status)["error"]
    assert "unreachable" in _run(control, room="кухня", device="light", action="on")["error"]


def test_registers_two_tools():
    registry = ToolRegistry()
    register(registry, FakeHA())
    assert [d.name for d in registry.definitions()] == ["get_home_status", "control_devices"]


def test_the_acs_own_range_is_checked_before_calling_it():
    """Seen live: 12 °C, confirmed, went to Home Assistant and failed there -
    the virtual AC only does 16-30 °C."""
    ha = FakeHA()
    ha.states[2]["attributes"].update(min_temp=16, max_temp=30)
    _, control = make_handlers(ha)
    result = _run(control, room="кухня", device="ac", action="on", temperature=12, confirmed=True)
    assert result["error"] == "The AC in Кухня can only be set to 16-30 °C." and ha.calls == []


def test_nulls_from_the_model_mean_not_given():
    ha = FakeHA()
    status, control = make_handlers(ha)
    assert set(_run(status, room=None)["rooms"]) == {"Кухня", "Спальня", "Коридор"}
    result = _run(control, room="кухня", device="light", action="on", brightness_pct=None, temperature=None, confirmed=None)
    assert "done" in result and ha.calls == [("light", "turn_on", "light.kitchen", None)]
