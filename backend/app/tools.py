"""Jarvis tools: thin, safety-checked wrappers over Home Assistant services.

Every write tool takes an optional `confirmed` flag. The model is instructed
(via the system prompt) to only set `confirmed=true` after the user has
explicitly agreed in conversation - this module enforces that the flag is
actually required in the cases docs/TZ.md calls out, rather than trusting the
model to remember on its own.
"""

from dataclasses import dataclass, field

from app.ha_client import HomeAssistantError, ha_client

# Room key -> display name. Entity ids in
# homeassistant/config/configuration.yaml follow this prefix convention.
ROOMS: dict[str, str] = {
    "living_room": "Гостиная",
    "bedroom": "Спальня",
    "kids_room": "Детская",
    "office": "Кабинет",
}

# Soft range: outside this, the tool requires confirmed=true.
TEMP_SOFT_MIN = 16.0
TEMP_SOFT_MAX = 28.0
# Hard range: rejected outright regardless of confirmation (sanity clamp).
TEMP_HARD_MIN = 5.0
TEMP_HARD_MAX = 35.0

MAX_BOOST_MINUTES = 60


@dataclass
class TurnContext:
    """Tracks state across one user turn (i.e. one call to JarvisAgent.chat),
    so the multi-room safety rule can see every write tool call made so far."""

    touched_rooms: set[str] = field(default_factory=set)


def _room_or_error(room: str) -> str | None:
    if room not in ROOMS:
        return f"Unknown room '{room}'. Valid rooms: {', '.join(ROOMS)}."
    return None


def _check_multi_room(room: str, confirmed: bool, ctx: TurnContext) -> str | None:
    other_rooms_already_touched = ctx.touched_rooms - {room}
    if other_rooms_already_touched and not confirmed:
        return (
            f"This turn already changed {sorted(other_rooms_already_touched)}. "
            f"Changing {room} too would affect multiple rooms at once - ask the "
            f"user to confirm before proceeding, then retry with confirmed=true."
        )
    return None


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
        "description": (
            "Set the target temperature for a room's climate control. Values "
            f"outside {TEMP_SOFT_MIN:g}-{TEMP_SOFT_MAX:g}°C require the user's "
            "explicit confirmation in the conversation before you pass confirmed=true."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "room": {"type": "string", "enum": list(ROOMS)},
                "target_temperature": {"type": "number", "description": "Desired temperature in °C."},
                "confirmed": {
                    "type": "boolean",
                    "description": "Set true only if the user has explicitly confirmed a value outside 16-28°C.",
                    "default": False,
                },
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
                "confirmed": {
                    "type": "boolean",
                    "description": "Set true if this is the second (or later) room changed in the same turn.",
                    "default": False,
                },
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
                "confirmed": {
                    "type": "boolean",
                    "description": "Set true if this is the second (or later) room changed in the same turn.",
                    "default": False,
                },
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
                    "description": f"Boost duration in minutes (1-{MAX_BOOST_MINUTES}).",
                    "default": 15,
                },
                "confirmed": {
                    "type": "boolean",
                    "description": "Set true if this is the second (or later) room changed in the same turn.",
                    "default": False,
                },
            },
            "required": ["room"],
        },
    },
]


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


async def _set_room_temperature(room: str, target_temperature: float, confirmed: bool, ctx: TurnContext) -> dict:
    if err := _room_or_error(room):
        return {"error": err}

    if target_temperature < TEMP_HARD_MIN or target_temperature > TEMP_HARD_MAX:
        return {
            "error": (
                f"{target_temperature}°C is outside the absolute safe range "
                f"({TEMP_HARD_MIN:g}-{TEMP_HARD_MAX:g}°C) and cannot be set, even with confirmation."
            )
        }

    if (target_temperature < TEMP_SOFT_MIN or target_temperature > TEMP_SOFT_MAX) and not confirmed:
        return {
            "error": (
                f"{target_temperature}°C is outside the normal {TEMP_SOFT_MIN:g}-{TEMP_SOFT_MAX:g}°C "
                "range. Ask the user to explicitly confirm this value, then retry with confirmed=true."
            )
        }

    if err := _check_multi_room(room, confirmed, ctx):
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

    ctx.touched_rooms.add(room)
    return {"ok": True, "room": room, "target_temperature_c": target_temperature}


async def _set_room_lights(room: str, on: bool, confirmed: bool, ctx: TurnContext) -> dict:
    if err := _room_or_error(room):
        return {"error": err}
    if err := _check_multi_room(room, confirmed, ctx):
        return {"error": err}

    try:
        await ha_client.call_service(
            "input_boolean",
            "turn_on" if on else "turn_off",
            entity_id=f"input_boolean.{room}_light",
        )
    except HomeAssistantError as exc:
        return {"error": str(exc)}

    ctx.touched_rooms.add(room)
    return {"ok": True, "room": room, "light_on": on}


async def _set_room_ac(room: str, on: bool, confirmed: bool, ctx: TurnContext) -> dict:
    if err := _room_or_error(room):
        return {"error": err}
    if err := _check_multi_room(room, confirmed, ctx):
        return {"error": err}

    try:
        await ha_client.call_service(
            "input_boolean",
            "turn_on" if on else "turn_off",
            entity_id=f"input_boolean.{room}_ac",
        )
    except HomeAssistantError as exc:
        return {"error": str(exc)}

    ctx.touched_rooms.add(room)
    return {"ok": True, "room": room, "ac_on": on}


async def _boost_ventilation(room: str, minutes: int, confirmed: bool, ctx: TurnContext) -> dict:
    if err := _room_or_error(room):
        return {"error": err}

    if minutes < 1 or minutes > MAX_BOOST_MINUTES:
        return {"error": f"minutes must be between 1 and {MAX_BOOST_MINUTES}."}

    if err := _check_multi_room(room, confirmed, ctx):
        return {"error": err}

    try:
        await ha_client.call_service(
            "script",
            f"boost_ventilation_{room}",
            data={"minutes": minutes},
        )
    except HomeAssistantError as exc:
        return {"error": str(exc)}

    ctx.touched_rooms.add(room)
    return {"ok": True, "room": room, "boost_minutes": minutes}


async def dispatch(tool_name: str, tool_input: dict, ctx: TurnContext) -> dict:
    if tool_name == "get_home_status":
        return await _get_home_status(tool_input.get("rooms"))
    if tool_name == "set_room_temperature":
        return await _set_room_temperature(
            room=tool_input["room"],
            target_temperature=float(tool_input["target_temperature"]),
            confirmed=bool(tool_input.get("confirmed", False)),
            ctx=ctx,
        )
    if tool_name == "set_room_lights":
        return await _set_room_lights(
            room=tool_input["room"],
            on=bool(tool_input["on"]),
            confirmed=bool(tool_input.get("confirmed", False)),
            ctx=ctx,
        )
    if tool_name == "set_room_ac":
        return await _set_room_ac(
            room=tool_input["room"],
            on=bool(tool_input["on"]),
            confirmed=bool(tool_input.get("confirmed", False)),
            ctx=ctx,
        )
    if tool_name == "boost_ventilation":
        return await _boost_ventilation(
            room=tool_input["room"],
            minutes=int(tool_input.get("minutes", 15)),
            confirmed=bool(tool_input.get("confirmed", False)),
            ctx=ctx,
        )
    return {"error": f"Unknown tool '{tool_name}'."}
