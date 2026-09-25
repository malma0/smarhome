"""Phase 3 domain (docs/TZ.md): the house, through Home Assistant.

Nothing about the house is written here. Rooms are Home Assistant areas and
devices are whatever sits in them - read fresh on every call, so a lamp
added to HA tomorrow (or a room renamed) needs no change to Jarvis. Today
that's the virtual house from homeassistant/virtual_house.py; real devices
later look exactly the same from here.

Device types, in the words the model uses:
- light       -> HA light (on/off, brightness)
- socket      -> HA switch
- ac          -> HA climate that can cool (air conditioner)
- heating     -> HA climate that only heats (radiator)
- ventilation -> HA fan
Sensors (temperature, humidity, CO2) are read-only, via get_home_status.

Norms (the resident's decision): each room has a temperature norm and a CO2
maximum, and the house keeps them by itself - heating, AC and ventilation
switch on and off on their own (Home Assistant automations, see
homeassistant/virtual_house.py), with no one being told "it's stuffy".
Jarvis reads the norms and changes them (set_room_norm: "сделай потеплее",
"держи в спальне 21"). They're HA number helpers in the room's area, found
by name: input_number.*_temperature_norm and input_number.*_co2_max.

Safety, in code (docs/TZ.md §7), not only in the prompt:
- AC temperature: 16-28 °C goes through, 5-35 °C only with the resident's
  confirmation, outside 5-35 °C never - not even confirmed.
- A second room in one turn needs confirmation for sockets and ACs - a
  socket can feed a heater or an iron, an AC is a sharp change. Lights
  don't: "выключи свет везде" on the way out is ordinary and reversible.
"""

import json

from app.ha_client import HomeAssistantClient, HomeAssistantError
from app.tools.registry import Tool, ToolRegistry, TurnContext

DEVICE_TYPES = ("light", "socket", "ac", "heating", "ventilation")
CLIMATE_TYPES = {"ac", "heating"}
GUARDED_TYPES = {"socket", "ac", "heating"}  # a second room in one turn needs confirmation
NORM_SUFFIXES = {"_temperature_norm": "temperature", "_co2_max": "co2_max"}

SAFE_TEMPERATURE = (16, 28)
ABSOLUTE_TEMPERATURE = (5, 35)

EVERYWHERE = {"all", "everywhere", "везде", "все", "всё", "весь дом", "дом"}
NO_ROOM = "Без комнаты"

_AREAS_TEMPLATE = (
    "{% set ns = namespace(items=[]) %}"
    "{% for s in states if s.domain in ['light', 'switch', 'climate', 'fan', 'sensor', 'input_number'] "
    "and not is_hidden_entity(s.entity_id) %}"
    "{% set ns.items = ns.items + [[s.entity_id, area_name(s.entity_id)]] %}"
    "{% endfor %}{{ ns.items | tojson }}"
)


async def _house(client: HomeAssistantClient) -> dict[str, list[dict]]:
    """room -> its devices and sensors, current state included."""
    areas = dict(json.loads(await client.render_template(_AREAS_TEMPLATE)))
    rooms: dict[str, list[dict]] = {}
    for state in await client.get_states():
        entity_id = state["entity_id"]
        if entity_id not in areas:
            continue  # hidden, or not a device kind this domain handles
        domain = entity_id.split(".")[0]
        attrs = state.get("attributes", {})
        if domain == "sensor" and not areas[entity_id]:
            continue  # Home Assistant's own sensors (sun, backups...) - not the house
        device = {"entity_id": entity_id, "name": attrs.get("friendly_name", entity_id), "state": state["state"]}
        if domain == "input_number":
            norm = next((k for suffix, k in NORM_SUFFIXES.items() if entity_id.endswith(suffix)), None)
            if norm is None or not areas[entity_id]:
                continue  # the virtual house's simulation knobs - not something to show or set
            device.update(type="norm", kind=norm, value=state["state"], unit=attrs.get("unit_of_measurement"),
                          min=attrs.get("min"), max=attrs.get("max"))
            del device["state"]
        elif domain == "sensor":
            device["type"] = "sensor"
            device["kind"] = attrs.get("device_class")
            device["value"] = state["state"]
            device["unit"] = attrs.get("unit_of_measurement")
            del device["state"]
        elif domain == "climate":
            device["type"] = "ac" if "cool" in (attrs.get("hvac_modes") or []) else "heating"
        else:
            device["type"] = {"light": "light", "switch": "socket", "fan": "ventilation"}[domain]
        if domain == "light" and state["state"] == "on" and attrs.get("brightness") is not None:
            device["brightness_pct"] = round(attrs["brightness"] / 255 * 100)
        if domain == "climate":
            device["target_temperature"] = attrs.get("temperature")
            device["current_temperature"] = attrs.get("current_temperature")
            device["action"] = attrs.get("hvac_action")
            device["min_temp"] = attrs.get("min_temp")
            device["max_temp"] = attrs.get("max_temp")
        rooms.setdefault(areas[entity_id] or NO_ROOM, []).append(device)
    return rooms


def _compact(devices: list[dict]) -> dict:
    """A room as the model reads it: {"light": "on 30%", "ac": "cool to 22 °C",
    "temperature": "25.5 °C", ...}. The full per-entity JSON of the virtual
    house was ~4300 characters, resent with every later turn - Groq's free
    tier allows 8000 tokens a minute, and two commands used it up."""
    room: dict = {}
    for device in devices:
        if device["type"] == "norm":
            value = f"{float(device['value']):g} {device.get('unit') or ''}".strip()
            room.setdefault("norm", {})[device["kind"]] = value
            continue
        if device["type"] == "sensor":
            key, value = device.get("kind") or "sensor", f"{device['value']} {device.get('unit') or ''}".strip()
        elif device["type"] in CLIMATE_TYPES:
            key = device["type"]
            target = device.get("target_temperature")
            mode = device["state"]
            value = f"{mode} to {target:g} °C" if mode != "off" and target is not None else mode
            if device.get("action") in ("cooling", "heating"):
                value += f", {device['action']} now"
        else:
            key = device["type"]
            value = device["state"]
            if device.get("brightness_pct") is not None:
                value += f" {device['brightness_pct']}%"
        if key in room:  # two lamps in one room: both, as a list
            room[key] = (room[key] if isinstance(room[key], list) else [room[key]]) + [value]
        else:
            room[key] = value
    return room


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 3 else word


def match_room(asked: str, rooms: list[str]) -> str | None:
    """'Кухня', 'кухне', 'на кухне', 'в зале' -> the room it names. Russian
    case endings differ in the last letter or two, so rooms match on a
    shared stem."""
    words = [w for w in asked.casefold().replace("ё", "е").split() if w not in {"в", "на", "во"}]
    for room in rooms:
        room_words = room.casefold().replace("ё", "е").split()
        if len(words) != len(room_words):
            continue
        if all(_same_stem(a, r) for a, r in zip(words, room_words)):
            return room
    return None


def _same_stem(a: str, b: str) -> bool:
    if a == b:
        return True
    sa, sb = _stem(a), _stem(b)
    common = 0
    for x, y in zip(sa, sb):
        if x != y:
            break
        common += 1
    return common >= max(3, min(len(sa), len(sb)) - 1)


def _temperature_error(temperature: float, confirmed: bool) -> str | None:
    low, high = ABSOLUTE_TEMPERATURE
    if not low <= temperature <= high:
        return f"{temperature} °C is outside the {low}-{high} °C the house allows at all - refused, even if confirmed."
    low, high = SAFE_TEMPERATURE
    if not low <= temperature <= high and not confirmed:
        return (
            f"{temperature} °C is outside the usual {low}-{high} °C - ask the resident to confirm, "
            "then retry with confirmed=true."
        )
    return None


async def _call(client, device: dict, device_type: str, action: str, brightness_pct, temperature) -> None:
    entity_id = device["entity_id"]
    if device_type == "light":
        if action == "off":
            await client.call_service("light", "turn_off", entity_id)
        else:
            data = {"brightness_pct": brightness_pct} if brightness_pct is not None else None
            await client.call_service("light", "turn_on", entity_id, data)
    elif device_type == "socket":
        await client.call_service("switch", "turn_off" if action == "off" else "turn_on", entity_id)
    elif device_type == "ventilation":
        await client.call_service("fan", "turn_off" if action == "off" else "turn_on", entity_id)
    elif action == "off":
        await client.call_service("climate", "set_hvac_mode", entity_id, {"hvac_mode": "off"})
    else:
        data = {"hvac_mode": "cool" if device_type == "ac" else "heat"}
        if temperature is not None:
            data["temperature"] = temperature
            await client.call_service("climate", "set_temperature", entity_id, data)
        else:
            await client.call_service("climate", "set_hvac_mode", entity_id, data)


def make_handlers(client: HomeAssistantClient):
    async def get_home_status(tool_input: dict, ctx: TurnContext) -> dict:
        try:
            rooms = await _house(client)
        except HomeAssistantError as exc:
            return {"error": f"Home Assistant is unreachable: {exc}"}
        asked = (tool_input.get("room") or "").strip()
        if asked and asked.casefold() not in EVERYWHERE:
            room = match_room(asked, list(rooms))
            if room is None:
                return {"error": f"No room called '{asked}'.", "rooms": sorted(rooms)}
            rooms = {room: rooms[room]}
        return {"rooms": {room: _compact(devices) for room, devices in rooms.items()}}

    async def control_devices(tool_input: dict, ctx: TurnContext) -> dict:
        device_type = tool_input.get("device")
        action = tool_input.get("action")
        if device_type not in DEVICE_TYPES or action not in ("on", "off"):
            return {"error": f"device must be one of {', '.join(DEVICE_TYPES)} and action on/off."}
        confirmed = bool(tool_input.get("confirmed", False))
        brightness_pct = tool_input.get("brightness_pct")
        temperature = tool_input.get("temperature")
        if brightness_pct is not None and device_type == "light":
            brightness_pct = max(1, min(100, int(brightness_pct)))
        if temperature is not None and device_type in CLIMATE_TYPES and action == "on":
            temperature = float(temperature)
            if err := _temperature_error(temperature, confirmed):
                return {"error": err}

        try:
            rooms = await _house(client)
        except HomeAssistantError as exc:
            return {"error": f"Home Assistant is unreachable: {exc}"}
        asked = (tool_input.get("room") or "").strip()
        if asked.casefold() in EVERYWHERE:
            targets = [r for r, devices in rooms.items() if any(d.get("type") == device_type for d in devices)]
        else:
            room = match_room(asked, list(rooms))
            if room is None:
                return {"error": f"No room called '{asked}'.", "rooms": sorted(rooms)}
            targets = [room]
        with_device = [r for r in targets if any(d.get("type") == device_type for d in rooms[r])]
        if not with_device:
            where = "any room" if len(targets) != 1 else targets[0]
            have = sorted(r for r, ds in rooms.items() if any(d.get("type") == device_type for d in ds))
            return {"error": f"There is no {device_type} in {where}.", "rooms_with_it": have}

        if device_type in CLIMATE_TYPES and action == "on" and temperature is not None:
            for room in with_device:
                for device in rooms[room]:
                    low, high = device.get("min_temp"), device.get("max_temp")
                    fits = low is None or high is None or low <= temperature <= high
                    if device.get("type") == device_type and not fits:
                        # The house would allow it (confirmed) - the unit itself can't.
                        name = "AC" if device_type == "ac" else device_type
                        return {"error": f"The {name} in {room} can only be set to {low:g}-{high:g} °C."}

        if device_type in GUARDED_TYPES and not confirmed:
            touched = {k.split(":", 1)[1] for k in ctx.touched if k.startswith(f"{device_type}:")}
            new_rooms = [r for r in with_device if r not in touched]
            if len(new_rooms) > 1 or (new_rooms and touched):
                return {
                    "error": (
                        f"That changes the {device_type} in more than one room - ask the resident to "
                        "confirm, then retry with confirmed=true."
                    ),
                    "rooms": with_device,
                    "already_changed_this_turn": sorted(touched),
                }

        done = []
        try:
            for room in with_device:
                for device in rooms[room]:
                    if device.get("type") == device_type:
                        await _call(client, device, device_type, action, brightness_pct, temperature)
                        done.append({"room": room, "device": device_type, "action": action})
                ctx.touched.add(f"{device_type}:{room}")
        except HomeAssistantError as exc:
            return {"error": f"Home Assistant refused: {exc}", "done": done}
        result = {"done": done}
        if brightness_pct is not None and device_type == "light" and action == "on":
            result["brightness_pct"] = brightness_pct
        if temperature is not None and device_type in CLIMATE_TYPES and action == "on":
            result["temperature"] = temperature
        return result

    async def set_room_norm(tool_input: dict, ctx: TurnContext) -> dict:
        wanted = {k: tool_input.get(k) for k in ("temperature", "co2_max") if tool_input.get(k) is not None}
        if not wanted:
            return {"error": "Give temperature and/or co2_max."}
        try:
            rooms = await _house(client)
        except HomeAssistantError as exc:
            return {"error": f"Home Assistant is unreachable: {exc}"}
        with_norms = [r for r, ds in rooms.items() if any(d.get("type") == "norm" for d in ds)]
        asked = (tool_input.get("room") or "").strip()
        if asked.casefold() in EVERYWHERE:
            targets = with_norms
        else:
            room = match_room(asked, list(rooms))
            if room is None:
                return {"error": f"No room called '{asked}'.", "rooms": sorted(rooms)}
            targets = [room]

        plan = []  # (room, norm entity, kind, value) - all checked before anything changes
        for room in targets:
            for kind, value in wanted.items():
                norm = next((d for d in rooms[room] if d.get("type") == "norm" and d["kind"] == kind), None)
                if norm is None:
                    return {"error": f"{room} has no {kind} norm.", "rooms_with_norms": sorted(with_norms)}
                value = float(value)
                low, high = norm.get("min"), norm.get("max")
                if low is not None and high is not None and not low <= value <= high:
                    unit = f" {norm['unit']}" if norm.get("unit") else ""
                    return {"error": f"The {kind} norm can be {low:g}-{high:g}{unit}, not {value:g}."}
                plan.append((room, norm["entity_id"], kind, value))

        done = []
        try:
            for room, entity_id, kind, value in plan:
                await client.call_service("input_number", "set_value", entity_id, {"value": value})
                done.append({"room": room, kind: value})
        except HomeAssistantError as exc:
            return {"error": f"Home Assistant refused: {exc}", "done": done}
        return {"done": done}

    return get_home_status, control_devices, set_room_norm


def register(registry: ToolRegistry, client: HomeAssistantClient | None = None) -> None:
    get_home_status, control_devices, set_room_norm = make_handlers(client or HomeAssistantClient())
    registry.register(
        Tool(
            name="get_home_status",
            description=(
                "What's in the house right now, room by room: lights (on/off, brightness), sockets, "
                "AC, heating, ventilation, sensors (temperature, humidity, CO2) and the room's norms - the "
                "temperature and CO2 maximum the house keeps by itself (heating, AC and ventilation run "
                "on their own to hold them; nobody needs to be told). Use it to answer questions about the house ('где жарче всего?', "
                "'что включено?', 'душно ли в спальне?') and to learn which rooms exist. The house "
                "changes all the time - call this for every such question instead of relying on an "
                "earlier answer. Answer with the facts; don't advise the resident to do things you can't do "
                "yourself (like airing a room). When the resident says it's stuffy, hot or cold, they're "
                "right whatever the sensors say - fix it (ventilation on, or set_room_norm), don't argue."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "room": {
                        "type": ["string", "null"],
                        "description": "Only this room (its name in any grammatical form). Omit for the whole house.",
                    }
                },
            },
            handler=get_home_status,
        )
    )
    registry.register(
        Tool(
            name="control_devices",
            description=(
                "Turn a device type on or off in a room right now: light (optionally with brightness_pct), "
                "socket, ventilation ('проветри' - it's also run automatically for the CO2 norm), ac (cools, "
                "optionally to temperature) or heating (heats, optionally to temperature). For 'warmer' / "
                "'cooler' use set_room_norm instead - heating and AC follow the norm. room is the room's name in "
                "any form ('кухня', 'на кухне'), or 'all' for the whole house ('выключи везде свет' is "
                "one call). For a few specific rooms, call once per room. Sockets, ACs and heating in a "
                "second room, and temperatures outside 16-28 °C, need the resident's confirmation - when the "
                "result says so, ask, and on a yes retry with confirmed=true. Always try the command: "
                "never tell the resident a room or device doesn't exist unless this tool said so."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "room": {"type": "string"},
                    "device": {"type": "string", "enum": list(DEVICE_TYPES)},
                    "action": {"type": "string", "enum": ["on", "off"]},
                    # nullable: models send null for "not given", and Groq rejects
                    # a call whose arguments don't match the schema
                    "brightness_pct": {"type": ["integer", "null"], "minimum": 1, "maximum": 100},
                    "temperature": {"type": ["number", "null"]},
                    "confirmed": {"type": ["boolean", "null"], "default": False},
                },
                "required": ["room", "device", "action"],
            },
            handler=control_devices,
        )
    )
    registry.register(
        Tool(
            name="set_room_norm",
            description=(
                "Change what the house keeps a room at: temperature (°C, 16-28) and/or co2_max (ppm, "
                "600-1500). Heating, AC and ventilation then hold it on their own. 'Сделай потеплее' / "
                "'прохладнее' without a number means the current norm (get_home_status) plus or minus "
                "1 °C; 'держи в спальне 21' sets 21. room is its name in any form, or 'all'. Say what "
                "the new norm is, briefly."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "room": {"type": "string"},
                    "temperature": {"type": ["number", "null"]},
                    "co2_max": {"type": ["number", "null"]},
                },
                "required": ["room"],
            },
            handler=set_room_norm,
        )
    )
