"""app.danger - no real Home Assistant: events and states are plain dicts."""

import asyncio

from app.danger import DangerWatcher, alert_text, danger_change, reflexes
from app.ha_client import HomeAssistantError


def _state(entity_id, state, **attrs):
    return {"entity_id": entity_id, "state": state, "attributes": attrs}


def _event(new, old):
    return {"data": {"new_state": new, "old_state": old}}


SMOKE_ON = _state("binary_sensor.kitchen_smoke", "on", device_class="smoke")
SMOKE_OFF = _state("binary_sensor.kitchen_smoke", "off", device_class="smoke")


def test_a_danger_sensor_going_on_or_off_is_a_change():
    assert danger_change(_event(SMOKE_ON, SMOKE_OFF)) == ("binary_sensor.kitchen_smoke", "smoke", True)
    assert danger_change(_event(SMOKE_OFF, SMOKE_ON)) == ("binary_sensor.kitchen_smoke", "smoke", False)


def test_what_is_not_news():
    assert danger_change(_event(SMOKE_ON, SMOKE_ON)) is None  # attributes only
    assert danger_change(_event(SMOKE_OFF, None)) is None  # appeared quiet
    assert danger_change(_event(SMOKE_OFF, _state("binary_sensor.kitchen_smoke", "unavailable"))) is None
    door = _state("binary_sensor.door", "on", device_class="door")
    assert danger_change(_event(door, _state("binary_sensor.door", "off"))) is None
    assert danger_change(_event(_state("switch.x", "on"), _state("switch.x", "off"))) is None
    assert danger_change({}) is None


def test_a_danger_already_on_at_start_is_raised():
    assert danger_change(_event(SMOKE_ON, None)) == ("binary_sensor.kitchen_smoke", "smoke", True)


def test_reflexes_are_reported_only_when_they_really_happened():
    house = [_state("switch.water_valve", "off"), _state("switch.gas_valve", "on"),
             _state("fan.kitchen_ventilation", "off"), _state("fan.office_ventilation", "off"),
             _state("light.kitchen", "on"), _state("light.office", "off")]
    assert reflexes("moisture", house) == ["Воду перекрыла."]
    assert reflexes("gas", house) == []  # gas valve still open - not claimed
    assert reflexes("smoke", house) == ["Вентиляцию остановила."]  # one light is still off


def test_alert_texts():
    assert alert_text("moisture", "Кухня", True, ["Воду перекрыла."]) == "Внимание! Протечка: кухня! Воду перекрыла."
    assert alert_text("smoke", "Зал", False, []) == "Отбой: дым, зал - датчик больше не срабатывает."


class FakeHA:
    def __init__(self, states=(), area="Кухня", down=False):
        self.states, self.area, self.down = list(states), area, down

    async def get_states(self):
        if self.down:
            raise HomeAssistantError("down")
        return self.states

    async def render_template(self, template):
        if self.down:
            raise HomeAssistantError("down")
        return self.area


def _watcher(ha):
    alerts = []
    return DangerWatcher(alerts.append, client=ha, websocket_url="ws://x", token="t", reflex_delay=0), alerts


def test_an_alert_says_where_and_what_the_house_did_and_isnt_repeated():
    watcher, alerts = _watcher(FakeHA([_state("switch.water_valve", "off")]))

    async def go():
        await watcher.handle("binary_sensor.kitchen_leak", "moisture", True)
        await watcher.handle("binary_sensor.kitchen_leak", "moisture", True)  # startup scan + event
        await watcher.handle("binary_sensor.kitchen_leak", "moisture", False)
        await watcher.handle("binary_sensor.kitchen_leak", "moisture", False)

    asyncio.run(go())
    assert [(a.active, a.text) for a in alerts] == [
        (True, "Внимание! Протечка: кухня! Воду перекрыла."),
        (False, "Отбой: протечка, кухня - датчик больше не срабатывает."),
    ]
    assert alerts[0].key == "moisture:Кухня"


def test_the_alarm_goes_off_even_if_home_assistant_stops_answering():
    watcher, alerts = _watcher(FakeHA(down=True))
    asyncio.run(watcher.handle("binary_sensor.x_smoke", "smoke", True))
    assert alerts[0].text == "Внимание! Дым: дом!"
