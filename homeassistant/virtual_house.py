"""The virtual house Jarvis is developed against - no real smart devices yet.

Each room gets real Home Assistant device types, so Jarvis talks to them
exactly as it will to real hardware later:
- light.<room>             - a lamp (on/off + brightness), template light
- switch.<room>_socket      - a socket, template switch
- climate.<room>_ac         - an air conditioner, generic_thermostat in AC mode
- sensor.<room>_temperature / _humidity / _co2
Behind them sit input_boolean / input_number helpers: the "physics". The
sensor helpers ("Симуляция: ...") are what you drag in the HA UI to pretend
it got hot or stuffy; the power helpers are hidden.

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

# Platforms whose entities this script owns (the rest - sun, backup,
# person... - belong to Home Assistant itself and are left alone).
OWNED_PLATFORMS = {"input_boolean", "input_number", "template", "generic_thermostat", "script"}




def _yaml() -> str:
    input_boolean, input_number = {}, {}
    lights, switches, sensors, climate = [], [], [], []

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
            "turn_on": [{"action": "input_boolean.turn_on", "target": {"entity_id": f"input_boolean.{slug}_light_power"}}],
            "turn_off": [{"action": "input_boolean.turn_off", "target": {"entity_id": f"input_boolean.{slug}_light_power"}}],
            "set_level": [
                {"action": "input_number.set_value",
                 "target": {"entity_id": f"input_number.{slug}_light_brightness"},
                 "data": {"value": "{{ brightness }}"}},
                {"action": "input_boolean.turn_on", "target": {"entity_id": f"input_boolean.{slug}_light_power"}},
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

        if full:
            input_boolean[f"{slug}_socket_power"] = {"name": f"{name}: питание розетки"}
            switches.append({
                "name": f"{name}: розетка",
                "unique_id": f"{slug}_socket",
                "state": f"{{{{ is_state('input_boolean.{slug}_socket_power', 'on') }}}}",
                "turn_on": [{"action": "input_boolean.turn_on", "target": {"entity_id": f"input_boolean.{slug}_socket_power"}}],
                "turn_off": [{"action": "input_boolean.turn_off", "target": {"entity_id": f"input_boolean.{slug}_socket_power"}}],
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
                "target_temp": 24,
                "initial_hvac_mode": "off",
                "precision": 0.5,
                "target_temp_step": 0.5,
            })

    config = {
        "default_config": None,
        "http": {"server_port": 8123},
        "input_boolean": input_boolean,
        "input_number": input_number,
        "template": [{"light": lights}, {"switch": switches}, {"sensor": sensors}],
        "climate": climate,
    }
    header = (
        "# GENERATED by homeassistant/virtual_house.py - edit ROOMS there, not this file.\n"
        "# The virtual house: real HA device types (light/switch/climate/sensor) over\n"
        "# helper entities, until real devices exist.\n\n"
    )
    body = yaml.safe_dump(config, allow_unicode=True, sort_keys=False, width=120)
    return header + body.replace("default_config: null", "default_config:")


def write() -> None:
    CONFIG.write_text(_yaml(), encoding="utf-8")
    print(f"wrote {CONFIG}")


def _token() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("HOME_ASSISTANT_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("HOME_ASSISTANT_TOKEN is not set in .env")


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

        # Entities: stable English ids, into their room, helpers hidden.
        wanted = {}
        for slug, _, full in ROOMS:
            wanted[("template", f"{slug}_light")] = (f"light.{slug}", slug, False)
            for kind in ("temperature", "humidity", "co2"):
                wanted[("template", f"{slug}_{kind}")] = (f"sensor.{slug}_{kind}", slug, False)
                wanted[("input_number", f"{slug}_{kind}_sim")] = (None, slug, False)
            wanted[("input_boolean", f"{slug}_light_power")] = (None, slug, True)
            wanted[("input_number", f"{slug}_light_brightness")] = (None, slug, True)
            if full:
                wanted[("template", f"{slug}_socket")] = (f"switch.{slug}_socket", slug, False)
                wanted[("generic_thermostat", f"{slug}_ac")] = (f"climate.{slug}_ac", slug, False)
                wanted[("input_boolean", f"{slug}_socket_power")] = (None, slug, True)
                wanted[("input_boolean", f"{slug}_ac_compressor")] = (None, slug, True)

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
            changes = {"area_id": area_id[slug], "hidden_by": "user" if hidden else None}
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
