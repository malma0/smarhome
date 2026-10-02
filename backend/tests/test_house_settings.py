"""The automation knobs the app changes: read from the helpers, written back
within their limits, and the norm "for all rooms" set in every room."""

import pytest

from app import house_settings


def _s(entity_id, state, **attrs):
    return {"entity_id": entity_id, "state": state, "attributes": attrs}


STATES = [
    _s("input_number.light_auto_off_minutes", "10.0"),
    _s("input_boolean.night_light", "on"),
    _s("input_number.night_light_pct", "30.0"),
    _s("input_datetime.night_light_start", "23:00:00"),
    _s("input_datetime.night_light_end", "07:00:00"),
    _s("input_text.night_light_rooms", "corridor,entrance"),
    _s("input_number.security_arm_delay_minutes", "2.0"),
    _s("input_number.security_entry_delay_minutes", "1.0"),
    _s("binary_sensor.corridor_motion", "off", friendly_name="Коридор: движение"),
    _s("binary_sensor.bedroom_motion", "off", friendly_name="Спальня: движение"),
    _s("input_number.corridor_temperature_norm", "22.0", min=16, max=28),
    _s("input_number.bedroom_temperature_norm", "21.0", min=16, max=28),
    _s("input_number.bedroom_co2_max", "800.0", min=500, max=1500),
]


def test_the_view_reads_the_helpers():
    v = house_settings.view(STATES)
    assert v["light_off_minutes"] == 10 and v["night_light"] is True and v["night_pct"] == 30
    assert v["night_start"] == "23:00" and v["night_end"] == "07:00" and v["night_rooms"] == ["corridor", "entrance"]
    assert v["arm_delay_minutes"] == 2 and v["entry_delay_minutes"] == 1
    assert v["rooms"] == [{"slug": "corridor", "name": "Коридор"}, {"slug": "bedroom", "name": "Спальня"}]
    assert v["norms"]["temperature"]["same"] is False and v["norms"]["temperature"]["rooms"] == 2
    assert v["norms"]["co2_max"] == {"value": 800.0, "same": True, "min": 500, "max": 1500, "rooms": 1}


def test_changes_stay_within_the_limits():
    assert house_settings.changes("light_off_minutes", 15, STATES) == [
        ("input_number", "set_value", "input_number.light_auto_off_minutes", {"value": 15})]
    with pytest.raises(ValueError):
        house_settings.changes("arm_delay_minutes", 30, STATES)
    assert house_settings.changes("night_start", "22:30", STATES)[0][3] == {"time": "22:30:00"}
    assert house_settings.changes("night_light", False, STATES)[0][1] == "turn_off"
    # a room the house doesn't have is dropped
    assert house_settings.changes("night_rooms", ["bedroom", "garage"], STATES)[0][3] == {"value": "bedroom"}
    with pytest.raises(ValueError):
        house_settings.changes("explode", 1, STATES)


def test_a_norm_for_all_rooms_sets_every_room_within_its_own_limits():
    calls = house_settings.changes("norm_temperature", 30, STATES)
    assert calls == [("input_number", "set_value", "input_number.corridor_temperature_norm", {"value": 28}),
                     ("input_number", "set_value", "input_number.bedroom_temperature_norm", {"value": 28})]
