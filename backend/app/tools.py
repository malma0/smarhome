"""Jarvis tools: thin wrappers over Home Assistant services.

This is the plain tool-use plumbing step: schemas + direct HA calls, no
safety validation yet. Range checks, the confirmation flow, and the
multi-room guard are added on top of this in a follow-up change - see
docs/TZ.md section 6 for the rules being enforced there.
"""

from app.ha_client import HomeAssistantError, ha_client

# Room key -> display name. Entity ids in
# homeassistant/config/configuration.yaml follow this prefix convention.
ROOMS: dict[str, str] = {
    "living_room": "Гостиная",
    "bedroom": "Спальня",
    "kids_room": "Детская",
    "office": "Кабинет",
}

TOOL_DEFINITIONS = [
    {
        "name": "get_home_status",
        "description": (
            "Get the current state snapshot (temperature, humidity, CO2, light, "
            "AC, ventilation boost) for one or more rooms. Call this whenever you "
            "need up-to-date information before answering or acting - do not guess."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "rooms": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(ROOMS)},
                    "description": "Rooms to query. Omit or leave empty for all rooms.",
                }
            },
        },
    },
    {
        "name": "set_room_temperature",
        "description": "Set the target temperature for a room's climate control.",
        "input_schema": {
            "type": "object",
            "properties": {
                "room": {"type": "string", "enum": list(ROOMS)},
                "target_temperature": {"type": "number", "description": "Desired temperature in °C."},
            },
            "required": ["room", "target_temperature"],
        },
    },
    {
        "name": "set_room_lights",
        "description": "Turn a room's lights on or off.",
        "input_schema": {
            "type": "object",
            "properties": {
                "room": {"type": "string", "enum": list(ROOMS)},
                "on": {"type": "boolean"},
            },
            "required": ["room", "on"],
        },
    },
    {
        "name": "set_room_ac",
        "description": "Turn a room's air conditioning unit on or off.",
        "input_schema": {
            "type": "object",
            "properties": {
                "room": {"type": "string", "enum": list(ROOMS)},
                "on": {"type": "boolean"},
            },
            "required": ["room", "on"],
        },
    },
    {
        "name": "boost_ventilation",
        "description": (
            "Temporarily boost ventilation in a room (e.g. because it feels stuffy "
            "or CO2 is high). This does NOT control oxygen percentage directly - "
            "there is no such control. It only raises air exchange for a limited time."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "room": {"type": "string", "enum": list(ROOMS)},
                "minutes": {
                    "type": "integer",
                    "description": "Boost duration in minutes.",
                    "default": 15,
                },
            },
            "required": ["room"],
        },
    },
]


def _room_or_error(room: str) -> str | None:
    if room not in ROOMS:
        return f"Unknown room '{room}'. Valid rooms: {', '.join(ROOMS)}."
    return None


async def _get_home_status(rooms: list[str] | None) -> dict:
    target_rooms = rooms or list(ROOMS)
    bad = [r for r in target_rooms if r not in ROOMS]
    if bad:
        return {"error": f"Unknown room(s): {', '.join(bad)}. Valid rooms: {', '.join(ROOMS)}."}

    try:
        states = await ha_client.get_states()
    except HomeAssistantError as exc:
        return {"error": str(exc)}

    by_entity = {s["entity_id"]: s["state"] for s in states}

    def num(entity_id: str) -> float | None:
        raw = by_entity.get(entity_id)
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    def flag(entity_id: str) -> bool | None:
        raw = by_entity.get(entity_id)
        if raw is None:
            return None
        return raw == "on"

    result = {}
    for room in target_rooms:
        result[room] = {
            "name": ROOMS[room],
            "temperature_c": num(f"sensor.{room}_temperature"),
            "target_temperature_c": num(f"input_number.{room}_target_temperature"),
            "humidity_pct": num(f"sensor.{room}_humidity"),
            "co2_ppm": num(f"sensor.{room}_co2"),
            "light_on": flag(f"input_boolean.{room}_light"),
            "ac_on": flag(f"input_boolean.{room}_ac"),
            "ventilation_boost_active": flag(f"input_boolean.{room}_ventilation_boost"),
        }
    return result


async def _set_room_temperature(room: str, target_temperature: float) -> dict:
    if err := _room_or_error(room):
        return {"error": err}
    try:
        await ha_client.call_service(
            "input_number",
            "set_value",
            entity_id=f"input_number.{room}_target_temperature",
            data={"value": target_temperature},
        )
    except HomeAssistantError as exc:
        return {"error": str(exc)}
    return {"ok": True, "room": room, "target_temperature_c": target_temperature}


async def _set_room_lights(room: str, on: bool) -> dict:
    if err := _room_or_error(room):
        return {"error": err}
    try:
        await ha_client.call_service(
            "input_boolean",
            "turn_on" if on else "turn_off",
            entity_id=f"input_boolean.{room}_light",
        )
    except HomeAssistantError as exc:
        return {"error": str(exc)}
    return {"ok": True, "room": room, "light_on": on}


async def _set_room_ac(room: str, on: bool) -> dict:
    if err := _room_or_error(room):
        return {"error": err}
    try:
        await ha_client.call_service(
            "input_boolean",
            "turn_on" if on else "turn_off",
            entity_id=f"input_boolean.{room}_ac",
        )
    except HomeAssistantError as exc:
        return {"error": str(exc)}
    return {"ok": True, "room": room, "ac_on": on}


async def _boost_ventilation(room: str, minutes: int) -> dict:
    if err := _room_or_error(room):
        return {"error": err}
    try:
        await ha_client.call_service(
            "script",
            f"boost_ventilation_{room}",
            data={"minutes": minutes},
        )
    except HomeAssistantError as exc:
        return {"error": str(exc)}
    return {"ok": True, "room": room, "boost_minutes": minutes}


async def dispatch(tool_name: str, tool_input: dict) -> dict:
    if tool_name == "get_home_status":
        return await _get_home_status(tool_input.get("rooms"))
    if tool_name == "set_room_temperature":
        return await _set_room_temperature(
            room=tool_input["room"],
            target_temperature=float(tool_input["target_temperature"]),
        )
    if tool_name == "set_room_lights":
        return await _set_room_lights(room=tool_input["room"], on=bool(tool_input["on"]))
    if tool_name == "set_room_ac":
        return await _set_room_ac(room=tool_input["room"], on=bool(tool_input["on"]))
    if tool_name == "boost_ventilation":
        return await _boost_ventilation(
            room=tool_input["room"],
            minutes=int(tool_input.get("minutes", 15)),
        )
    return {"error": f"Unknown tool '{tool_name}'."}
