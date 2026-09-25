"""The virtual house Jarvis is developed against - no real smart devices yet.

Each room gets real Home Assistant device types, so Jarvis talks to them
exactly as it will to real hardware later:
- light.<room>              - a lamp (on/off + brightness), template light
- switch.<room>_socket      - a socket, template switch
- climate.<room>_ac         - an air conditioner (cools), generic_thermostat
- climate.<room>_heating    - heating (a radiator), generic_thermostat
- fan.<room>_ventilation    - ventilation (in a real house: a supply unit,
                              a breather or a window drive), template fan
- sensor.<room>_temperature / _humidity / _co2
- binary_sensor.<room>_smoke, and in the kitchen binary_sensor.kitchen_leak
  and binary_sensor.kitchen_gas - the dangers
- switch.water_valve / switch.gas_valve - main valves (on = open)
Behind them sit input_boolean / input_number helpers: the "physics". The
sensor helpers ("Симуляция: ...") are what you drag in the HA UI to pretend
it got hot or stuffy; the power helpers are hidden.

The house keeps its norms by itself - the resident's decision: Jarvis
doesn't tell anyone a room is stuffy, it airs it. Per room:
- input_number.<room>_temperature_norm - heating holds norm - 0.5 °C, the
  AC norm + 0.5 °C (automation "Норма температуры")
- input_number.<room>_co2_max - ventilation on above it, off 150 ppm under
  it (automation "Норма CO2", every minute and on every change)
Home Assistant enforces them, so they hold with Jarvis switched off too;
Jarvis sets them ("держи в спальне 21", "сделай потеплее").

Dangers get the house's own reflexes first (automations, instant, Jarvis or
no Jarvis): a leak closes the water, gas closes the gas, smoke stops all
ventilation (it feeds a fire and spreads smoke - the CO2 norm stays off
while smoke is detected) and turns every light on to see the way out.
Jarvis raises the alarm (app/danger.py). "Симуляция: дым, Кухня" and the
like set them off from the HA UI.

"Физика" runs every minute: CO2 creeps up (people breathe), ventilation
brings it down; rooms drift toward a cool autumn outside, heating and the AC
push back - so the norms visibly work.

Rooms become HA areas - Jarvis finds "свет на кухне" by asking Home
Assistant what's in the Кухня area, not from a list in its own code. A real
lamp added later just needs to be put into its area.

Usage (from the repo root, HA running, HOME_ASSISTANT_TOKEN in .env):
    python homeassistant/virtual_house.py write   # -> homeassistant/config/configuration.yaml
    docker restart jarvis-homeassistant
    python homeassistant/virtual_house.py setup   # areas, entity ids, cleanup
"""

import asyncio
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "homeassistant" / "config" / "configuration.yaml"

# Where the one-off danger sensors and the main valves are.
LEAK_ROOM = GAS_ROOM = VALVE_ROOM = "kitchen"

# (slug, name, has sockets and an AC) - preliminary, will change.
ROOMS = [
    ("entrance", "Прихожая", False),
    ("bedroom", "Спальня", True),
    ("office", "Кабинет", True),
    ("living_room", "Зал", True),
    ("corridor", "Коридор", False),
    ("kitchen", "Кухня", True),
]
# Different starting readings, so "где жарче всего?" has an answer.
START = {
    "entrance": (20.5, 40, 450),
    "bedroom": (22.5, 48, 900),
    "office": (24.0, 38, 1100),
    "living_room": (23.0, 45, 650),
    "corridor": (21.0, 42, 500),
    "kitchen": (25.5, 55, 800),
}
DEFAULT_TEMPERATURE_NORM = 22
DEFAULT_CO2_MAX = 800
NORM_BAND = 0.5  # heating holds norm - 0.5, the AC norm + 0.5
CO2_HYSTERESIS = 150  # ventilation stops this far under the max

# The physics, per minute.
OUTSIDE = 19.0  # rooms drift toward it: a cool autumn
DRIFT = 0.02  # share of the gap to OUTSIDE closed each minute
HEATER_STEP = 0.4
AC_STEP = 0.4
CO2_BREATHING = 15
CO2_VENTILATION = 90

# Platforms whose entities this script owns (the rest - sun, backup,
# person... - belong to Home Assistant itself and are left alone).
OWNED_PLATFORMS = {"input_boolean", "input_number", "template", "generic_thermostat", "script", "automation"}


def _toggle(entity: str, on: bool) -> list[dict]:
    return [{"action": f"input_boolean.turn_{'on' if on else 'off'}", "target": {"entity_id": entity}}]


def _yaml() -> str:
    input_boolean, input_number = {}, {}
    lights, switches, fans, sensors, climate, automations = [], [], [], [], [], []
    binary_sensors = []
    physics = []
    no_smoke = " and ".join(f"is_state('binary_sensor.{slug}_smoke', 'off')" for slug, _, _ in ROOMS)

    def danger_sensor(slug: str, room: str, kind: str, label: str, device_class: str) -> None:
        input_boolean[f"{slug}_{kind}_sim"] = {"name": f"Симуляция: {label}, {room}"}
        binary_sensors.append({
            "name": f"{room}: {label}",
            "unique_id": f"{slug}_{kind}",
            "state": f"{{{{ is_state('input_boolean.{slug}_{kind}_sim', 'on') }}}}",
            "device_class": device_class,
        })

    for valve, label in (("water_valve", "кран воды"), ("gas_valve", "кран газа")):
        input_boolean[f"{valve}_open"] = {"name": f"Главный {label}: открыт", "initial": True}
        switches.append({
            "name": f"Главный {label}",
            "unique_id": valve,
            "state": f"{{{{ is_state('input_boolean.{valve}_open', 'on') }}}}",
            "turn_on": _toggle(f"input_boolean.{valve}_open", True),
            "turn_off": _toggle(f"input_boolean.{valve}_open", False),
        })

    for slug, name, full in ROOMS:
        temperature, humidity, co2 = START[slug]

        input_boolean[f"{slug}_light_power"] = {"name": f"{name}: питание света"}
        input_number[f"{slug}_light_brightness"] = {
            "name": f"{name}: яркость света", "min": 1, "max": 255, "step": 1, "initial": 255,
        }
        lights.append({
            "name": f"{name}: свет",
            "unique_id": f"{slug}_light",
            "state": f"{{{{ is_state('input_boolean.{slug}_light_power', 'on') }}}}",
            "level": f"{{{{ states('input_number.{slug}_light_brightness') | int }}}}",
            "turn_on": _toggle(f"input_boolean.{slug}_light_power", True),
            "turn_off": _toggle(f"input_boolean.{slug}_light_power", False),
            "set_level": [
                {"action": "input_number.set_value",
                 "target": {"entity_id": f"input_number.{slug}_light_brightness"},
                 "data": {"value": "{{ brightness }}"}},
                *_toggle(f"input_boolean.{slug}_light_power", True),
            ],
        })

        for kind, label, unit, device_class, lo, hi, step, value in (
            ("temperature", "температура", "°C", "temperature", 5, 40, 0.1, temperature),
            ("humidity", "влажность", "%", "humidity", 10, 90, 1, humidity),
            ("co2", "CO2", "ppm", "carbon_dioxide", 350, 5000, 10, co2),
        ):
            input_number[f"{slug}_{kind}_sim"] = {
                "name": f"Симуляция: {name}, {label}", "min": lo, "max": hi, "step": step,
                "initial": value, "unit_of_measurement": unit, "mode": "box",
            }
            sensors.append({
                "name": f"{name}: {label}",
                "unique_id": f"{slug}_{kind}",
                "state": f"{{{{ states('input_number.{slug}_{kind}_sim') }}}}",
                "unit_of_measurement": unit,
                "device_class": device_class,
                "state_class": "measurement",
            })

        danger_sensor(slug, name, "smoke", "дым", "smoke")
        if slug == LEAK_ROOM:
            danger_sensor(slug, name, "leak", "протечка", "moisture")
        if slug == GAS_ROOM:
            danger_sensor(slug, name, "gas", "газ", "gas")

        # --- the norms and what keeps them ---
        input_number[f"{slug}_temperature_norm"] = {
            "name": f"{name}: норма температуры", "min": 16, "max": 28, "step": 0.5,
            "initial": DEFAULT_TEMPERATURE_NORM, "unit_of_measurement": "°C", "mode": "box",
        }
        input_number[f"{slug}_co2_max"] = {
            "name": f"{name}: норма CO2 (не выше)", "min": 600, "max": 1500, "step": 50,
            "initial": DEFAULT_CO2_MAX, "unit_of_measurement": "ppm", "mode": "box",
        }

        input_boolean[f"{slug}_heater_power"] = {"name": f"{name}: нагрев отопления"}
        climate.append({
            "platform": "generic_thermostat",
            "name": f"{name}: отопление",
            "unique_id": f"{slug}_heating",
            "heater": f"input_boolean.{slug}_heater_power",
            "target_sensor": f"sensor.{slug}_temperature",
            "min_temp": 5,
            "max_temp": 30,
            "target_temp": DEFAULT_TEMPERATURE_NORM - NORM_BAND,
            "initial_hvac_mode": "heat",
            "precision": 0.5,
            "target_temp_step": 0.5,
        })

        input_boolean[f"{slug}_ventilation_power"] = {"name": f"{name}: мотор вентиляции"}
        fans.append({
            "name": f"{name}: вентиляция",
            "unique_id": f"{slug}_ventilation",
            "state": f"{{{{ is_state('input_boolean.{slug}_ventilation_power', 'on') }}}}",
            "turn_on": _toggle(f"input_boolean.{slug}_ventilation_power", True),
            "turn_off": _toggle(f"input_boolean.{slug}_ventilation_power", False),
        })

        if full:
            input_boolean[f"{slug}_socket_power"] = {"name": f"{name}: питание розетки"}
            switches.append({
                "name": f"{name}: розетка",
                "unique_id": f"{slug}_socket",
                "state": f"{{{{ is_state('input_boolean.{slug}_socket_power', 'on') }}}}",
                "turn_on": _toggle(f"input_boolean.{slug}_socket_power", True),
                "turn_off": _toggle(f"input_boolean.{slug}_socket_power", False),
            })
            input_boolean[f"{slug}_ac_compressor"] = {"name": f"{name}: компрессор кондиционера"}
            climate.append({
                "platform": "generic_thermostat",
                "name": f"{name}: кондиционер",
                "unique_id": f"{slug}_ac",
                "heater": f"input_boolean.{slug}_ac_compressor",
                "target_sensor": f"sensor.{slug}_temperature",
                "ac_mode": True,
                "min_temp": 16,
                "max_temp": 30,
                "target_temp": DEFAULT_TEMPERATURE_NORM + NORM_BAND,
                "initial_hvac_mode": "cool",
                "precision": 0.5,
                "target_temp_step": 0.5,
            })

        norm = f"states('input_number.{slug}_temperature_norm') | float({DEFAULT_TEMPERATURE_NORM})"
        keep_temperature = [{
            "action": "climate.set_temperature",
            "target": {"entity_id": f"climate.{slug}_heating"},
            "data": {"temperature": f"{{{{ {norm} - {NORM_BAND} }}}}"},
        }]
        if full:
            keep_temperature.append({
                "action": "climate.set_temperature",
                "target": {"entity_id": f"climate.{slug}_ac"},
                "data": {"temperature": f"{{{{ {norm} + {NORM_BAND} }}}}"},
            })
        automations.append({
            "id": f"{slug}_temperature_norm",
            "alias": f"{name}: норма температуры",
            "mode": "restart",
            "triggers": [
                {"trigger": "state", "entity_id": f"input_number.{slug}_temperature_norm"},
                {"trigger": "homeassistant", "event": "start"},
            ],
            "actions": keep_temperature,
        })

        co2_now = f"states('sensor.{slug}_co2') | float(0)"
        co2_max = f"states('input_number.{slug}_co2_max') | float({DEFAULT_CO2_MAX})"
        automations.append({
            "id": f"{slug}_co2_norm",
            "alias": f"{name}: норма CO2",
            "mode": "restart",
            "triggers": [
                {"trigger": "time_pattern", "minutes": "/1"},
                {"trigger": "state", "entity_id": [f"sensor.{slug}_co2", f"input_number.{slug}_co2_max"]},
                {"trigger": "homeassistant", "event": "start"},
            ],
            # Never during a fire: ventilation feeds it and spreads the smoke.
            "conditions": [{"condition": "template", "value_template": f"{{{{ {no_smoke} }}}}"}],
            "actions": [{
                "choose": [
                    {"conditions": [{"condition": "template", "value_template": f"{{{{ {co2_now} > {co2_max} }}}}"}],
                     "sequence": [{"action": "fan.turn_on", "target": {"entity_id": f"fan.{slug}_ventilation"}}]},
                    {"conditions": [{"condition": "template",
                                     "value_template": f"{{{{ {co2_now} < {co2_max} - {CO2_HYSTERESIS} }}}}"}],
                     "sequence": [{"action": "fan.turn_off", "target": {"entity_id": f"fan.{slug}_ventilation"}}]},
                ],
            }],
        })

        # --- the physics of this room, one step a minute ---
        t = f"states('input_number.{slug}_temperature_sim') | float({temperature})"
        heat = f"({HEATER_STEP} if is_state('input_boolean.{slug}_heater_power', 'on') else 0)"
        cool = f"({AC_STEP} if is_state('input_boolean.{slug}_ac_compressor', 'on') else 0)" if full else "0"
        physics.append({
            "action": "input_number.set_value",
            "target": {"entity_id": f"input_number.{slug}_temperature_sim"},
            "data": {"value": f"{{{{ ([[{t} + ({OUTSIDE} - {t}) * {DRIFT} + {heat} - {cool}, 5] | max, 40] | min) | round(1) }}}}"},
        })
        c = f"states('input_number.{slug}_co2_sim') | float({co2})"
        vent = f"({CO2_VENTILATION} if is_state('input_boolean.{slug}_ventilation_power', 'on') else 0)"
        physics.append({
            "action": "input_number.set_value",
            "target": {"entity_id": f"input_number.{slug}_co2_sim"},
            "data": {"value": f"{{{{ [[{c} + {CO2_BREATHING} - {vent}, 400] | max, 5000] | min }}}}"},
        })

    automations += [
        {
            "id": "danger_leak",
            "alias": "Опасность: протечка - перекрыть воду",
            "triggers": [{"trigger": "state", "entity_id": f"binary_sensor.{LEAK_ROOM}_leak", "to": "on"}],
            "actions": [{"action": "switch.turn_off", "target": {"entity_id": "switch.water_valve"}}],
        },
        {
            "id": "danger_gas",
            "alias": "Опасность: газ - перекрыть газ",
            "triggers": [{"trigger": "state", "entity_id": f"binary_sensor.{GAS_ROOM}_gas", "to": "on"}],
            "actions": [{"action": "switch.turn_off", "target": {"entity_id": "switch.gas_valve"}}],
        },
        {
            "id": "danger_smoke",
            "alias": "Опасность: дым - вентиляцию стоп, свет везде",
            "triggers": [{"trigger": "state", "entity_id": [f"binary_sensor.{slug}_smoke" for slug, _, _ in ROOMS],
                          "to": "on"}],
            "actions": [
                {"action": "fan.turn_off", "target": {"entity_id": [f"fan.{slug}_ventilation" for slug, _, _ in ROOMS]}},
                {"action": "light.turn_on", "target": {"entity_id": [f"light.{slug}" for slug, _, _ in ROOMS]},
                 "data": {"brightness_pct": 100}},
            ],
        },
    ]

    automations.append({
        "id": "virtual_house_physics",
        "alias": "Виртуальный дом: физика",
        "triggers": [{"trigger": "time_pattern", "minutes": "/1"}],
        "actions": physics,
    })

    config = {
        "default_config": None,
        "http": {"server_port": 8123},
        "input_boolean": input_boolean,
        "input_number": input_number,
        "template": [{"light": lights}, {"switch": switches}, {"fan": fans}, {"sensor": sensors},
                     {"binary_sensor": binary_sensors}],
        "climate": climate,
        "automation": automations,
    }
    header = (
        "# GENERATED by homeassistant/virtual_house.py - edit ROOMS there, not this file.\n"
        "# The virtual house: real HA device types (light/switch/fan/climate/sensor/binary_sensor)\n"
        "# over helper entities, per-room norms the house keeps by itself, danger reflexes,\n"
        "# and a little physics.\n\n"
    )
    body = yaml.safe_dump(config, allow_unicode=True, sort_keys=False, width=200)
    return header + body.replace("default_config: null", "default_config:")


def write() -> None:
    CONFIG.write_text(_yaml(), encoding="utf-8")
    print(f"wrote {CONFIG}")


def _token() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("HOME_ASSISTANT_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("HOME_ASSISTANT_TOKEN is not set in .env")


def _wanted() -> dict[tuple[str, str], tuple[str | None, str, bool]]:
    """(platform, unique_id) -> (entity id to give it or None, room slug, hidden)."""
    wanted = {}
    for slug, _, full in ROOMS:
        wanted[("template", f"{slug}_light")] = (f"light.{slug}", slug, False)
        wanted[("template", f"{slug}_ventilation")] = (f"fan.{slug}_ventilation", slug, False)
        wanted[("generic_thermostat", f"{slug}_heating")] = (f"climate.{slug}_heating", slug, False)
        for kind in ("temperature", "humidity", "co2"):
            wanted[("template", f"{slug}_{kind}")] = (f"sensor.{slug}_{kind}", slug, False)
            wanted[("input_number", f"{slug}_{kind}_sim")] = (None, slug, False)
        wanted[("input_number", f"{slug}_temperature_norm")] = (None, slug, False)
        wanted[("input_number", f"{slug}_co2_max")] = (None, slug, False)
        wanted[("input_boolean", f"{slug}_light_power")] = (None, slug, True)
        wanted[("input_number", f"{slug}_light_brightness")] = (None, slug, True)
        wanted[("input_boolean", f"{slug}_heater_power")] = (None, slug, True)
        wanted[("input_boolean", f"{slug}_ventilation_power")] = (None, slug, True)
        wanted[("automation", f"{slug}_temperature_norm")] = (None, slug, False)
        wanted[("automation", f"{slug}_co2_norm")] = (None, slug, False)
        wanted[("template", f"{slug}_smoke")] = (f"binary_sensor.{slug}_smoke", slug, False)
        wanted[("input_boolean", f"{slug}_smoke_sim")] = (None, slug, False)
        if slug == LEAK_ROOM:
            wanted[("template", f"{slug}_leak")] = (f"binary_sensor.{slug}_leak", slug, False)
            wanted[("input_boolean", f"{slug}_leak_sim")] = (None, slug, False)
        if slug == GAS_ROOM:
            wanted[("template", f"{slug}_gas")] = (f"binary_sensor.{slug}_gas", slug, False)
            wanted[("input_boolean", f"{slug}_gas_sim")] = (None, slug, False)
        if full:
            wanted[("template", f"{slug}_socket")] = (f"switch.{slug}_socket", slug, False)
            wanted[("generic_thermostat", f"{slug}_ac")] = (f"climate.{slug}_ac", slug, False)
            wanted[("input_boolean", f"{slug}_socket_power")] = (None, slug, True)
            wanted[("input_boolean", f"{slug}_ac_compressor")] = (None, slug, True)
    wanted[("automation", "virtual_house_physics")] = (None, None, False)
    for valve in ("water_valve", "gas_valve"):
        wanted[("template", valve)] = (f"switch.{valve}", VALVE_ROOM, False)
        wanted[("input_boolean", f"{valve}_open")] = (None, VALVE_ROOM, True)
    for automation in ("danger_leak", "danger_gas", "danger_smoke"):
        wanted[("automation", automation)] = (None, None, False)
    return wanted


async def setup(url: str = "ws://localhost:8123/api/websocket") -> None:
    import websockets

    async with websockets.connect(url, max_size=None) as ws:
        counter = iter(range(1, 1_000_000))

        async def call(**msg):
            i = next(counter)
            await ws.send(json.dumps({"id": i, **msg}))
            while True:
                reply = json.loads(await ws.recv())
                if reply.get("id") == i:
                    if not reply.get("success", True):
                        raise RuntimeError(f"{msg['type']}: {reply.get('error')}")
                    return reply.get("result")

        await ws.recv()
        await ws.send(json.dumps({"type": "auth", "access_token": _token()}))
        if json.loads(await ws.recv())["type"] != "auth_ok":
            raise SystemExit("Home Assistant rejected the token")

        # Areas: one per room; empty ones that aren't rooms (the defaults HA
        # made during setup, rooms since removed) go.
        areas = {a["name"]: a for a in await call(type="config/area_registry/list")}
        entities = await call(type="config/entity_registry/list")
        devices = await call(type="config/device_registry/list")
        used = {e["area_id"] for e in entities} | {d["area_id"] for d in devices}
        room_names = {name for _, name, _ in ROOMS}
        for name, area in areas.items():
            if name not in room_names and area["area_id"] not in used:
                await call(type="config/area_registry/delete", area_id=area["area_id"])
                print(f"removed empty area {name}")
        area_id = {}
        for slug, name, _ in ROOMS:
            area = areas.get(name) or await call(type="config/area_registry/create", name=name)
            area_id[slug] = area["area_id"]

        wanted = _wanted()

        # Stale ones first (from an older version of this file - the 4-room
        # prototype): they may hold the ids the new entities are renamed to.
        # A removed entity keeps a placeholder state ("restored") after restarts.
        live = {s["entity_id"] for s in await call(type="get_states") if not s["attributes"].get("restored")}
        for entry in entities:
            key = (entry["platform"], entry["unique_id"])
            if key not in wanted and entry["platform"] in OWNED_PLATFORMS and entry["entity_id"] not in live:
                await call(type="config/entity_registry/remove", entity_id=entry["entity_id"])
                print(f"removed stale {entry['entity_id']}")

        found = set()
        for entry in await call(type="config/entity_registry/list"):
            key = (entry["platform"], entry["unique_id"])
            if key not in wanted:
                continue
            found.add(key)
            entity_id, slug, hidden = wanted[key]
            changes = {"area_id": area_id.get(slug), "hidden_by": "user" if hidden else None}
            if entity_id and entry["entity_id"] != entity_id:
                changes["new_entity_id"] = entity_id
            await call(type="config/entity_registry/update", entity_id=entry["entity_id"], **changes)
        missing = set(wanted) - found
        if missing:
            print(f"not in Home Assistant yet (restart it after `write`?): {sorted(missing)}")
        print(f"rooms: {len(ROOMS)}, entities set up: {len(found)}")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "write":
        write()
    elif command == "setup":
        asyncio.run(setup())
    else:
        raise SystemExit(__doc__)
