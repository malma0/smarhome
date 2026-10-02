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
- humidifier  -> HA humidifier (keeps the room's humidity minimum)
- curtains    -> HA cover (on = open, position 0-100)
- water_valve / gas_valve -> HA switch.*water_valve / *gas_valve (on = open)
- security    -> input_boolean.security_armed, the house's guard (on = armed)
Sensors (temperature, humidity, CO2, movement, windows, doors, the house's
power and electricity) and danger sensors (smoke, leak, gas, CO, movement
in the armed house - raised by app.danger on their own) are read-only, via
get_home_status; what they were earlier comes from home_history.
Schedules ("Расписание: ..." automations) are listed and changed by
house_schedule.

Norms (the resident's decision): each room has a temperature norm and a CO2
maximum, and the house keeps them by itself - heating, AC and ventilation
switch on and off on their own (Home Assistant automations, see
homeassistant/virtual_house.py), with no one being told "it's stuffy".
Jarvis reads the norms and changes them (set_room_norm: "сделай потеплее",
"держи в спальне 21"). They're HA number helpers in the room's area, found
by name: input_number.*_temperature_norm and input_number.*_co2_max.

Scenarios ("Я ушёл", "Спокойной ночи"...) are Home Assistant scripts - the
resident edits them in HA's own script editor. run_scenario finds one by its
name or by the phrases listed in its description ("Фразы: я ушёл, я
ухожу, ..."), runs it and passes on what it does.

Safety, in code (docs/TZ.md §7), not only in the prompt:
- AC temperature: 16-28 °C goes through, 5-35 °C only with the resident's
  confirmation, outside 5-35 °C never - not even confirmed.
- A second room in one turn needs confirmation for sockets and ACs - a
  socket can feed a heater or an iron, an AC is a sharp change. Lights
  don't: "выключи свет везде" on the way out is ordinary and reversible.
"""

import json
from datetime import datetime, timedelta

from app.ha_client import HomeAssistantClient, HomeAssistantError
from app.tools.registry import Tool, ToolRegistry, TurnContext

DEVICE_TYPES = ("light", "socket", "ac", "heating", "ventilation", "humidifier", "curtains", "water_valve",
                "gas_valve", "security")
VALVE_TYPES = {"water_valve", "gas_valve"}
HOUSE_TYPES = VALVE_TYPES | {"security"}  # one per house - found wherever they are, the room doesn't matter
SECURITY_ENTITY = "input_boolean.security_armed"
HOUSE = "Весь дом"  # where house-wide things (the guard, electricity) are listed
# House-wide sensors, by entity id -> the name the model reads.
HOUSE_SENSORS = {"sensor.house_power": "power_now", "sensor.house_energy_today": "electricity_today",
                 "sensor.house_energy_month": "electricity_this_month"}
OPENING_CLASSES = {"window", "door"}
# The danger that makes opening a valve risky: water with a leak on needs a
# yes; gas with gas detected is refused outright, and a yes is needed anyway.
VALVE_DANGERS = {"water_valve": ("moisture",), "gas_valve": ("gas", "carbon_monoxide")}
DANGER_CLASSES = ("smoke", "moisture", "gas", "carbon_monoxide", "safety")  # safety: the guard's alarm
CLIMATE_TYPES = {"ac", "heating"}
GUARDED_TYPES = {"socket", "ac", "heating"}  # a second room in one turn needs confirmation
NORM_SUFFIXES = {"_temperature_norm": "temperature", "_co2_max": "co2_max", "_humidity_min": "humidity_min"}

SAFE_TEMPERATURE = (16, 28)
ABSOLUTE_TEMPERATURE = (5, 35)

EVERYWHERE = {"all", "everywhere", "везде", "все", "всё", "весь дом", "дом"}
NO_ROOM = "Без комнаты"

_AREAS_TEMPLATE = (
    "{% set ns = namespace(items=[]) %}"
    "{% for s in states if s.domain in ['light', 'switch', 'climate', 'fan', 'sensor', 'binary_sensor', "
    "'input_number', 'humidifier', 'cover', 'input_boolean'] "
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
        if domain == "input_boolean" and entity_id != SECURITY_ENTITY:
            continue  # the virtual house's knobs - only the guard is a device
        if domain == "sensor" and not areas[entity_id] and entity_id not in HOUSE_SENSORS:
            continue  # Home Assistant's own sensors (sun, backups...) - not the house
        device = {"entity_id": entity_id, "name": attrs.get("friendly_name", entity_id), "state": state["state"]}
        if domain == "input_number":
            norm = next((k for suffix, k in NORM_SUFFIXES.items() if entity_id.endswith(suffix)), None)
            if norm is None or not areas[entity_id]:
                continue  # the virtual house's simulation knobs - not something to show or set
            device.update(type="norm", kind=norm, value=state["state"], unit=attrs.get("unit_of_measurement"),
                          min=attrs.get("min"), max=attrs.get("max"))
            del device["state"]
        elif domain == "binary_sensor":
            kind = attrs.get("device_class")
            if kind in DANGER_CLASSES:
                device["type"] = "danger"
            elif kind == "motion" or kind in OPENING_CLASSES:
                device["type"] = kind
            else:
                continue
            device["kind"] = kind
        elif entity_id == SECURITY_ENTITY:
            device["type"] = "security"
        elif domain == "humidifier":
            device.update(type="humidifier", target_humidity=attrs.get("humidity"), action=attrs.get("action"))
        elif domain == "cover":
            device.update(type="curtains", position=attrs.get("current_position"))
        elif entity_id in HOUSE_SENSORS:
            device.update(type="house_sensor", kind=HOUSE_SENSORS[entity_id], value=state["state"],
                          unit=attrs.get("unit_of_measurement"))
            del device["state"]
        elif domain == "sensor":
            device["type"] = "sensor"
            device["kind"] = attrs.get("device_class")
            device["value"] = state["state"]
            device["unit"] = attrs.get("unit_of_measurement")
            del device["state"]
        elif domain == "climate":
            device["type"] = "ac" if "cool" in (attrs.get("hvac_modes") or []) else "heating"
        elif domain == "switch" and entity_id.endswith(("water_valve", "gas_valve")):
            device["type"] = "water_valve" if entity_id.endswith("water_valve") else "gas_valve"
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
        house_wide = device["type"] in ("security", "house_sensor") and not areas[entity_id]
        rooms.setdefault(HOUSE if house_wide else areas[entity_id] or NO_ROOM, []).append(device)
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
        if device["type"] == "danger":
            kind = "intrusion" if device["kind"] == "safety" else device["kind"]
            room.setdefault("danger_sensors", {})[kind] = "DETECTED" if device["state"] == "on" else "clear"
            continue
        if device["type"] == "motion":
            room["movement"] = "now" if device["state"] == "on" else "none"
            continue
        if device["type"] in OPENING_CLASSES:
            room[device["type"]] = "open" if device["state"] == "on" else "closed"
            continue
        if device["type"] == "security":
            room["security"] = "armed" if device["state"] == "on" else "off"
            continue
        if device["type"] == "house_sensor":
            room[device["kind"]] = f"{device['value']} {device.get('unit') or ''}".strip()
            continue
        if device["type"] == "humidifier":
            target = device.get("target_humidity")
            value = device["state"] + (f", keeps {target:g} %" if device["state"] == "on" and target is not None else "")
            if device.get("action") == "humidifying":
                value += ", humidifying now"
            room["humidifier"] = value
            continue
        if device["type"] == "curtains":
            position = device.get("position")
            room["curtains"] = "closed" if not position else "open" if position >= 100 else f"open {position:g}%"
            continue
        if device["type"] in VALVE_TYPES:
            room[device["type"]] = "open" if device["state"] == "on" else "closed"
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


def _words(text: str) -> str:
    text = text.casefold().replace("ё", "е")
    text = "".join(c if c.isalnum() or c.isspace() else " " for c in text)
    return " ".join(w for w in text.split() if w not in ("джарвис", "джервис"))


def _scenario_phrases(description: str) -> list[str]:
    """'Фразы: я ушёл, я ухожу. Гасит свет...' -> ['я ушёл', 'я ухожу']."""
    head, found, rest = description.partition("Фразы:")
    if not found:
        return []
    return [p.strip() for p in rest.split(".", 1)[0].split(",") if p.strip()]


def _scenario_does(description: str) -> str:
    """The description without its phrase list - what the scenario does."""
    head, found, rest = description.partition("Фразы:")
    return (head + (rest.split(".", 1)[1] if found and "." in rest else "")).strip() if found else description.strip()


_FILLER = {"я", "мы", "ну", "вот", "уже", "всё", "все", "а", "и", "же", "ведь", "пожалуйста"}


def match_scenario(asked: str, scenarios: list[dict]) -> dict | None:
    """By name or any of its phrases, ignoring case, punctuation and 'Джарвис'.
    Exact first; then the words that matter, by stem, so "вернулся" finds
    "я вернулся" and "ну всё, я ушла" finds "я ушёл" (found while building
    training data: people drop the "я")."""
    wanted = _words(asked)
    for scenario in scenarios:
        if wanted in {_words(scenario["name"]), *(_words(p) for p in scenario["phrases"])}:
            return scenario
    said = {_stem(w) for w in wanted.split() if w not in _FILLER}
    if not said:
        return None
    for scenario in scenarios:
        for phrase in (scenario["name"], *scenario["phrases"]):
            words = {_stem(w) for w in _words(phrase).split() if w not in _FILLER}
            if words and (words <= said or said <= words):
                return scenario
    return None


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


async def _call(client, device: dict, device_type: str, action: str, brightness_pct, temperature,
                position=None) -> None:
    entity_id = device["entity_id"]
    if device_type == "curtains":
        if position is not None:
            await client.call_service("cover", "set_cover_position", entity_id, {"position": position})
        else:
            await client.call_service("cover", "open_cover" if action == "on" else "close_cover", entity_id)
    elif device_type == "humidifier":
        await client.call_service("humidifier", "turn_off" if action == "off" else "turn_on", entity_id)
    elif device_type == "security":
        await client.call_service("input_boolean", "turn_off" if action == "off" else "turn_on", entity_id)
    elif device_type == "light":
        if action == "off":
            await client.call_service("light", "turn_off", entity_id)
        else:
            data = {"brightness_pct": brightness_pct} if brightness_pct is not None else None
            await client.call_service("light", "turn_on", entity_id, data)
    elif device_type == "socket":
        await client.call_service("switch", "turn_off" if action == "off" else "turn_on", entity_id)
    elif device_type == "ventilation":
        await client.call_service("fan", "turn_off" if action == "off" else "turn_on", entity_id)
    elif device_type in VALVE_TYPES:
        await client.call_service("switch", "turn_off" if action == "off" else "turn_on", entity_id)
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
        position = tool_input.get("position")
        if position is not None and device_type == "curtains":
            position = max(0, min(100, int(position)))
            action = "on" if position > 0 else "off"
        else:
            position = None
        if temperature is not None and device_type in CLIMATE_TYPES and action == "on":
            temperature = float(temperature)
            if err := _temperature_error(temperature, confirmed):
                return {"error": err}

        try:
            rooms = await _house(client)
        except HomeAssistantError as exc:
            return {"error": f"Home Assistant is unreachable: {exc}"}
        asked = (tool_input.get("room") or "").strip()
        if asked.casefold() in EVERYWHERE or device_type in HOUSE_TYPES:
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

        if device_type in VALVE_TYPES and action == "on":
            detected = [
                (r, d["kind"]) for r, ds in rooms.items() for d in ds
                if d.get("type") == "danger" and d["kind"] in VALVE_DANGERS[device_type] and d["state"] == "on"
            ]
            if detected and device_type == "gas_valve":
                return {"error": "Gas is still detected - the gas stays closed, even if confirmed.", "detected": detected}
            if (detected or device_type == "gas_valve") and not confirmed:
                reason = "a leak is still detected" if detected else "opening the gas always needs a yes"
                return {"error": f"Opening the {device_type}: {reason} - ask the resident to confirm, "
                                 "then retry with confirmed=true."}

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
                        await _call(client, device, device_type, action, brightness_pct, temperature, position)
                        done.append({"room": room, "device": device_type, "action": action})
                ctx.touched.add(f"{device_type}:{room}")
        except HomeAssistantError as exc:
            return {"error": f"Home Assistant refused: {exc}", "done": done}
        result = {"done": done}
        if brightness_pct is not None and device_type == "light" and action == "on":
            result["brightness_pct"] = brightness_pct
        if temperature is not None and device_type in CLIMATE_TYPES and action == "on":
            result["temperature"] = temperature
        if position is not None:
            result["position"] = position
        return result

    async def set_room_norm(tool_input: dict, ctx: TurnContext) -> dict:
        wanted = {k: tool_input.get(k) for k in ("temperature", "co2_max", "humidity_min")
                  if tool_input.get(k) is not None}
        if not wanted:
            return {"error": "Give temperature, co2_max and/or humidity_min."}
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

    async def run_scenario(tool_input: dict, ctx: TurnContext) -> dict:
        try:
            scripts = [s for s in await client.get_states() if s["entity_id"].startswith("script.")]
            scenarios = []
            for state in scripts:
                object_id = state["entity_id"].split(".", 1)[1]
                try:
                    description = (await client.get_script_config(object_id)).get("description") or ""
                except HomeAssistantError:
                    description = ""  # a script defined outside scripts.yaml has no editable config
                scenarios.append({
                    "id": object_id,
                    "name": state["attributes"].get("friendly_name", object_id),
                    "phrases": _scenario_phrases(description),
                    "does": _scenario_does(description),
                })
        except HomeAssistantError as exc:
            return {"error": f"Home Assistant is unreachable: {exc}"}
        listing = [{"name": s["name"], "phrases": s["phrases"]} for s in scenarios]
        asked = (tool_input.get("name") or "").strip()
        if not asked:
            return {"scenarios": listing}
        scenario = match_scenario(asked, scenarios)
        if scenario is None:
            return {"error": f"No scenario called '{asked}'.", "scenarios": listing}
        try:
            await client.call_service("script", scenario["id"])  # waits until it has run
        except HomeAssistantError as exc:
            return {"error": f"Home Assistant refused: {exc}"}
        return {"ran": scenario["name"], "does": scenario["does"]}

    return get_home_status, control_devices, set_room_norm, run_scenario


HISTORY_KINDS = {"temperature": "temperature", "humidity": "humidity", "co2": "carbon_dioxide"}
ENERGY_ENTITY = "sensor.house_energy"
SCHEDULE_PREFIX = "Расписание:"


PERIODS = ("last_hour", "last_3_hours", "last_24_hours", "last_night", "today", "yesterday", "this_week",
           "this_month", "last_month")


def period_span(period: str, now: datetime) -> tuple[datetime, datetime]:
    """'за эту неделю' -> Monday 00:00 .. now. Worked out here, not by the model:
    the home model got "which date was Monday" wrong (2 of 3 history misses)."""
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month = day.replace(day=1)
    spans = {
        "last_hour": (now - timedelta(hours=1), now),
        "last_3_hours": (now - timedelta(hours=3), now),
        "last_24_hours": (now - timedelta(hours=24), now),
        # Before 8 it's the night still going on; later, the one that ended this morning.
        "last_night": (day, day + timedelta(hours=7)) if now.hour >= 8 else (day, now),
        "today": (day, now),
        "yesterday": (day - timedelta(days=1), day),
        "this_week": (day - timedelta(days=now.weekday()), now),
        "this_month": (month, now),
        "last_month": ((month - timedelta(days=1)).replace(day=1), month),
    }
    return spans[period]


def _local(at: str | None, now: datetime) -> datetime | None:
    """'2026-10-01T03:00' (local, as the model writes it) -> aware datetime."""
    if not at:
        return None
    when = datetime.fromisoformat(str(at).strip().replace(" ", "T"))
    return when.replace(tzinfo=now.tzinfo) if when.tzinfo is None else when


def _points(series: list[dict], start: datetime) -> list[tuple[datetime, float]]:
    points = []
    for item in series:
        try:
            value = float(item["state"])
        except (TypeError, ValueError, KeyError):
            continue  # unknown / unavailable
        at = max(datetime.fromisoformat(item["last_changed"]), start)  # the state at the start began earlier
        points.append((at, value))
    return points


def summarize(points: list[tuple[datetime, float]], end: datetime, tz) -> dict:
    """min and max with when, and the time-weighted average - a value held all night counts for all night."""
    fmt = lambda t: t.astimezone(tz).strftime("%Y-%m-%d %H:%M")  # noqa: E731
    low = min(points, key=lambda p: p[1])
    high = max(points, key=lambda p: p[1])
    spans = [(value, ((points[i + 1][0] if i + 1 < len(points) else end) - at).total_seconds())
             for i, (at, value) in enumerate(points)]
    total = sum(seconds for _, seconds in spans)
    average = sum(v * s for v, s in spans) / total if total > 0 else points[-1][1]
    return {"min": {"value": low[1], "at": fmt(low[0])}, "max": {"value": high[1], "at": fmt(high[0])},
            "average": round(average, 1), "last": points[-1][1]}


def _schedule_matches(asked: str, name: str) -> bool:
    said = {_stem(w) for w in _words(asked).split() if w not in _FILLER}
    words = {_stem(w) for w in _words(name).split() if w not in _FILLER}
    return bool(said) and (said <= words or words <= said)


# Words that put a question about the sensors in the past - word beginnings ("сейчас" is not
# "час"). Without any of them "сколько градусов в спальне" is about now; the home model
# sometimes reached for the history anyway.
_PAST_STEMS = ("был", "ноч", "вчера", "позавчера", "утр", "вечер", "днем", "сутк", "недел", "месяц", "час",
               "сегодня", "раньше", "недавно", "прошл", "максим", "миним", "средн", "самая", "самый", "поднима",
               "опуска", "падал", "менял", "истори", "понедельн")
_PAST_WORDS = {"за", "до", "после"}  # not "с": "как там с влажностью" is now


def asks_about_the_past(said: str) -> bool:
    return any(w in _PAST_WORDS or w.startswith(_PAST_STEMS) for w in _words(said).split())


def make_more_handlers(client: HomeAssistantClient, now=lambda: datetime.now().astimezone()):
    get_home_status = make_handlers(client)[0]

    async def home_history(tool_input: dict, ctx: TurnContext) -> dict:
        what = tool_input.get("what") or "temperature"
        if what in HISTORY_KINDS and ctx.said and not asks_about_the_past(ctx.said):
            # no time in the words: it's the reading now - the status, as for any "сколько градусов"
            return await get_home_status({"room": tool_input.get("room")} if tool_input.get("room") else {}, ctx)
        current = now()
        period = tool_input.get("period")
        try:
            if period:
                if period not in PERIODS:
                    return {"error": f"period must be one of {', '.join(PERIODS)}."}
                start, end = period_span(period, current)
            else:
                end = _local(tool_input.get("end"), current) or current
                start = _local(tool_input.get("start"), current) or end - timedelta(hours=24)
        except ValueError:
            return {"error": "Times as local 'YYYY-MM-DDTHH:MM'."}
        if start >= end:
            return {"error": "start must be before end."}
        unit = "kWh"
        try:
            if what == "electricity":
                entity_id, room = ENERGY_ENTITY, HOUSE
            elif what in HISTORY_KINDS:
                rooms = await _house(client)
                room = match_room((tool_input.get("room") or "").strip(), list(rooms))
                if room is None:
                    return {"error": f"No room called '{tool_input.get('room')}'.", "rooms": sorted(rooms)}
                sensor = next((d for d in rooms[room] if d.get("type") == "sensor"
                               and d.get("kind") == HISTORY_KINDS[what]), None)
                if sensor is None:
                    return {"error": f"{room} has no {what} sensor."}
                entity_id, unit = sensor["entity_id"], sensor.get("unit")
            else:
                return {"error": "what must be temperature, humidity, co2 or electricity."}
            series = await client.get_history(entity_id, start.isoformat(), end.isoformat())
        except HomeAssistantError as exc:
            return {"error": f"Home Assistant is unreachable: {exc}"}
        points = _points(series, start)
        period = {"from": start.strftime("%Y-%m-%d %H:%M"), "to": end.strftime("%Y-%m-%d %H:%M")}
        if not points:
            return {"error": "No records for that time - the house keeps 60 days.", **period}
        if what == "electricity":
            return {"what": "electricity", **period, "used_kwh": round(points[-1][1] - points[0][1], 2)}
        return {"room": room, "what": what, "unit": unit, **period, **summarize(points, end, current.tzinfo)}

    async def house_schedule(tool_input: dict, ctx: TurnContext) -> dict:
        action = tool_input.get("action") or "list"
        try:
            states = await client.get_states()
        except HomeAssistantError as exc:
            return {"error": f"Home Assistant is unreachable: {exc}"}
        times = {s["entity_id"]: s["state"] for s in states if s["entity_id"].startswith("input_datetime.")}
        schedules = []
        for state in states:
            name = state.get("attributes", {}).get("friendly_name", "")
            if state["entity_id"].startswith("automation.") and name.startswith(SCHEDULE_PREFIX):
                time_helper = f"input_datetime.{state['entity_id'].split('.', 1)[1]}_time"
                schedules.append({"entity_id": state["entity_id"], "name": name[len(SCHEDULE_PREFIX):].strip(),
                                  "on": state["state"] == "on", "time_helper": time_helper if time_helper in times else None,
                                  "time": times.get(time_helper, "")[:5] or None})
        public = lambda s: {k: s[k] for k in ("name", "on", "time")}  # noqa: E731
        if action == "list":
            return {"schedules": [public(s) for s in schedules]}
        asked = (tool_input.get("name") or "").strip()
        schedule = next((s for s in schedules if _schedule_matches(asked, s["name"])), None)
        if schedule is None:
            return {"error": f"No schedule called '{asked}'.", "schedules": [public(s) for s in schedules]}
        try:
            if action in ("enable", "disable"):
                await client.call_service("automation", "turn_on" if action == "enable" else "turn_off",
                                          schedule["entity_id"])
                schedule["on"] = action == "enable"
            elif action == "set_time":
                if schedule["time_helper"] is None:
                    return {"error": f"'{schedule['name']}' has no set time (it follows the sun)."}
                at = str(tool_input.get("time") or "").strip()
                try:
                    hours, minutes = (int(x) for x in at.split(":")[:2])
                    assert 0 <= hours < 24 and 0 <= minutes < 60
                except (ValueError, AssertionError):
                    return {"error": "time as 'HH:MM'."}
                await client.call_service("input_datetime", "set_datetime", schedule["time_helper"],
                                          {"time": f"{hours:02d}:{minutes:02d}:00"})
                schedule["time"] = f"{hours:02d}:{minutes:02d}"
            else:
                return {"error": "action must be list, enable, disable or set_time."}
        except HomeAssistantError as exc:
            return {"error": f"Home Assistant refused: {exc}"}
        return {"done": public(schedule)}

    return home_history, house_schedule


def register(registry: ToolRegistry, client: HomeAssistantClient | None = None) -> None:
    client = client or HomeAssistantClient()
    get_home_status, control_devices, set_room_norm, run_scenario = make_handlers(client)
    home_history, house_schedule = make_more_handlers(client)
    registry.register(
        Tool(
            name="get_home_status",
            description=(
                "The house now, room by room: lights, sockets, AC, heating, ventilation, humidifiers, curtains, "
                "sensors (temperature, humidity, CO2, movement, windows, doors), danger sensors, main valves, and "
                "each room's norms (temperature, CO2 max, humidity min) the house keeps by itself; under 'Весь "
                "дом' the guard (security) and electricity (power now, today, this month). Call it for every question about the house "
                "- it changes. Answer with facts; don't advise what you can't do yourself (like airing a "
                "room). If the resident says it's stuffy, hot or cold, they're right - fix it (ventilation "
                "on, or set_room_norm), don't argue."
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
                "Turn a device type on/off in a room: light (brightness_pct), socket, ventilation, ac (cools, "
                "optional temperature), heating (optional temperature), humidifier, curtains (on = open, off = "
                "close, position 0-100 for 'наполовину'), water_valve/gas_valve (on = open, room ignored), "
                "security - the guard (on = armed, off = disarmed, room ignored). room in any form ('на кухне') or 'all' - the whole house in one call. "
                "'Warmer'/'cooler' is set_room_norm, not this. Needs the resident's yes, then retry with "
                "confirmed=true: sockets/AC/heating in a second room, temperatures outside 16-28 °C, gas, "
                "water during a leak. Always try - never say a room or device is missing unless this tool "
                "says so."
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
                    "position": {"type": ["integer", "null"], "minimum": 0, "maximum": 100},
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
                "What the house keeps a room at: temperature (16-28 °C), co2_max (600-1500 ppm), humidity_min "
                "(30-60 %, rooms with a humidifier) - heating, AC, ventilation and humidifiers follow. 'Потеплее'/'прохладнее' = the current norm "
                "(get_home_status) ±1; 'держи 21' = 21. room in any form or 'all'."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "room": {"type": "string"},
                    "temperature": {"type": ["number", "null"]},
                    "co2_max": {"type": ["number", "null"]},
                    "humidity_min": {"type": ["number", "null"]},
                },
                "required": ["room"],
            },
            handler=set_room_norm,
        )
    )
    registry.register(
        Tool(
            name="run_scenario",
            description=(
                "Run the resident's scenario ('Я ушёл', 'Я дома', 'Спокойной ночи', 'Доброе утро'...) by the "
                "phrase they said; no name lists them. Then say briefly what it did ('does')."
            ),
            parameters={
                "type": "object",
                "properties": {"name": {"type": ["string", "null"]}},
            },
            handler=run_scenario,
        )
    )
    registry.register(
        Tool(
            name="home_history",
            description=(
                "What the house's sensors were earlier: what temperature, humidity or co2 in a room - min and max "
                "with when, the average; electricity - kWh used in the period (the whole house). period: "
                "last_hour, last_3_hours, last_24_hours, last_night ('ночью'), today, yesterday, this_week, "
                "this_month, last_month - the dates are worked out for you. Only for another span: start/end as "
                "local 'YYYY-MM-DDTHH:MM'. Now is get_home_status, not this."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "what": {"type": "string", "enum": ["temperature", "humidity", "co2", "electricity"]},
                    "room": {"type": ["string", "null"]},
                    "period": {"type": ["string", "null"], "enum": [*PERIODS, None]},
                    "start": {"type": ["string", "null"]},
                    "end": {"type": ["string", "null"]},
                },
                "required": ["what"],
            },
            handler=home_history,
        )
    )
    registry.register(
        Tool(
            name="house_schedule",
            description=(
                "The house's schedules - what runs by itself at a time or at sunset ('«Доброе утро» по будням', "
                "'свет в прихожей на закате'). list; enable / disable by name; set_time 'HH:MM' by name. Not "
                "reminders to the resident (that's reminders)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "enable", "disable", "set_time"]},
                    "name": {"type": ["string", "null"]},
                    "time": {"type": ["string", "null"]},
                },
                "required": ["action"],
            },
            handler=house_schedule,
        )
    )
