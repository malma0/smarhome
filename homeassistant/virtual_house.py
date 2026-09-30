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

Scenarios ("Я ушёл", "Я дома", "Спокойной ночи", "Доброе утро") are
ordinary Home Assistant scripts in homeassistant/config/scripts.yaml - the
file HA's own script editor saves to, so the resident edits them with the
mouse (Настройки -> Автоматизации и сцены -> Скрипты). `write` creates it
with the defaults only if it doesn't exist yet: edits are never overwritten.
The words that start one ("Фразы: ...") live in the script's description.

"Физика" runs every minute: CO2 creeps up (people breathe), ventilation
brings it down; rooms drift toward a cool autumn outside, heating and the AC
push back - so the norms visibly work.

Around the norms and dangers, the house also:
- keeps a humidity minimum where there's a humidifier (generic_hygrostat),
  as silently as the CO2 norm;
- has motion sensors everywhere: at night the corridor and the hall light
  up to 30% on movement and go dark 2 min after; any room's light goes off
  after 10 min without movement;
- has windows (and the front door in the hall): an open window pauses the
  room's heating and AC, closing it brings back what was there;
- guards itself: armed 2 min after "Я ушёл", disarmed by "Я дома";
  movement or an opening in the armed house is a danger after a minute's
  grace (binary_sensor.<room>_intrusion, raised by app/danger.py);
- has curtains where there are windows: "Доброе утро" opens them all,
  "Спокойной ночи" closes them all - automations on the scripts running, so
  the resident's scripts.yaml stays theirs;
- runs schedules ("Расписание: ..." automations): "Доброе утро" at 7:00 on
  weekdays (input_datetime.schedule_good_morning_time), the hall light at
  sunset;
- meters electricity: sensor.house_power from what's on, sensor.house_energy
  and its day/month meters. History is kept 60 days (recorder).

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
SCRIPTS = ROOT / "homeassistant" / "config" / "scripts.yaml"
AWAY_TEMPERATURE = 18  # "Я ушёл": the house is kept at this, ACs off
NIGHT_DROP = 2  # "Спокойной ночи": the bedroom norm goes down this much

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

# What else rooms have (all virtual): windows and curtains where there's a
# window, a humidifier where people sit or sleep, the front door in the hall.
WINDOWS = ("bedroom", "office", "living_room", "kitchen")
CURTAINS = WINDOWS
HUMIDIFIERS = ("bedroom", "office", "living_room")
DOORS = ("entrance",)
NIGHT_LIGHT_ROOMS = ("corridor", "entrance")
NIGHT_LIGHT_PCT = 30
NIGHT = ("23:00:00", "07:00:00")
DEFAULT_HUMIDITY_MIN = 40
HUMIDITY_BAND = 2  # the humidifier starts this far under the minimum
AUTO_OFF_MINUTES = 10  # no movement this long - the room's light goes off
ENTRY_DELAY = "00:01:00"  # movement in the armed house: this long to say "Я дома"
ARM_DELAY = "00:02:00"  # "Я ушёл": armed after this, time to walk out
GOOD_MORNING_TIME = "07:00:00"
# Watts when on, for the electricity estimate. Heating is central (the flat's
# utilities count it), so it uses no electricity here.
WATTS = {"light": 60, "socket": 100, "ac": 900, "heating": 0, "ventilation": 40, "humidifier": 30}

# The physics, per minute.
OUTSIDE = 19.0  # rooms drift toward it: a cool autumn
DRIFT = 0.02  # share of the gap to OUTSIDE closed each minute
HEATER_STEP = 0.4
AC_STEP = 0.4
CO2_BREATHING = 15
CO2_VENTILATION = 90
WINDOW_DRIFT = 4  # an open window: the room goes to the outside this many times faster
WINDOW_CO2 = 60
OUTSIDE_HUMIDITY = 35  # autumn with the heating on: dry
HUMIDITY_DRIFT = 0.01
HUMIDIFIER_STEP = 1.0
VENTILATION_DRYING = 0.5

# Platforms whose entities this script owns (the rest - sun, backup,
# person... - belong to Home Assistant itself and are left alone).
OWNED_PLATFORMS = {"input_boolean", "input_number", "input_datetime", "template", "generic_thermostat",
                   "generic_hygrostat", "integration", "utility_meter", "script", "automation"}


def _toggle(entity: str, on: bool) -> list[dict]:
    return [{"action": f"input_boolean.turn_{'on' if on else 'off'}", "target": {"entity_id": entity}}]


def _yaml() -> str:
    input_boolean, input_number, input_datetime = {}, {}, {}
    lights, switches, fans, sensors, climate, automations = [], [], [], [], [], []
    binary_sensors, covers, hygrostats = [], [], []
    physics, power = [], []
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

        # --- movement, windows and doors, the guard ---
        input_boolean[f"{slug}_motion_sim"] = {"name": f"Симуляция: движение, {name}"}
        binary_sensors.append({
            "name": f"{name}: движение", "unique_id": f"{slug}_motion", "device_class": "motion",
            "state": f"{{{{ is_state('input_boolean.{slug}_motion_sim', 'on') }}}}",
        })
        for opening, label in (("window", "окно"), ("door", "дверь")):
            if slug in (WINDOWS if opening == "window" else DOORS):
                input_boolean[f"{slug}_{opening}_sim"] = {"name": f"Симуляция: {label} открыто, {name}"}
                binary_sensors.append({
                    "name": f"{name}: {label}", "unique_id": f"{slug}_{opening}", "device_class": opening,
                    "state": f"{{{{ is_state('input_boolean.{slug}_{opening}_sim', 'on') }}}}",
                })
        input_boolean[f"{slug}_intrusion_flag"] = {"name": f"{name}: тревога охраны"}
        binary_sensors.append({
            "name": f"{name}: движение в пустом доме", "unique_id": f"{slug}_intrusion", "device_class": "safety",
            "state": f"{{{{ is_state('input_boolean.{slug}_intrusion_flag', 'on') }}}}",
        })

        if slug in HUMIDIFIERS:
            input_number[f"{slug}_humidity_min"] = {
                "name": f"{name}: норма влажности (не ниже)", "min": 30, "max": 60, "step": 1,
                "initial": DEFAULT_HUMIDITY_MIN, "unit_of_measurement": "%", "mode": "box",
            }
            input_boolean[f"{slug}_humidifier_power"] = {"name": f"{name}: мотор увлажнителя"}
            hygrostats.append({
                "name": f"{name}: увлажнитель", "unique_id": f"{slug}_humidifier",
                "humidifier": f"input_boolean.{slug}_humidifier_power", "target_sensor": f"sensor.{slug}_humidity",
                "device_class": "humidifier", "min_humidity": 30, "max_humidity": 70,
                "target_humidity": DEFAULT_HUMIDITY_MIN, "dry_tolerance": HUMIDITY_BAND,
                "wet_tolerance": HUMIDITY_BAND, "initial_state": True,
            })
            automations.append({
                "id": f"{slug}_humidity_norm",
                "alias": f"{name}: норма влажности",
                "mode": "restart",
                "triggers": [
                    {"trigger": "state", "entity_id": f"input_number.{slug}_humidity_min"},
                    {"trigger": "homeassistant", "event": "start"},
                ],
                "actions": [{
                    "action": "humidifier.set_humidity",
                    "target": {"entity_id": f"humidifier.{slug}_humidifier"},
                    "data": {"humidity": f"{{{{ states('input_number.{slug}_humidity_min') | int({DEFAULT_HUMIDITY_MIN}) }}}}"},
                }],
            })

        if slug in CURTAINS:
            input_number[f"{slug}_curtain_position"] = {
                "name": f"{name}: положение штор", "min": 0, "max": 100, "step": 1, "unit_of_measurement": "%",
            }
            position = f"input_number.{slug}_curtain_position"

            def set_position(value: str, position=position) -> list[dict]:
                return [{"action": "input_number.set_value", "target": {"entity_id": position}, "data": {"value": value}}]

            covers.append({
                "name": f"{name}: шторы", "unique_id": f"{slug}_curtains", "device_class": "curtain",
                "position": f"{{{{ states('{position}') | int(0) }}}}",
                "open_cover": set_position("100"), "close_cover": set_position("0"),
                "set_cover_position": set_position("{{ position }}"),
            })

        on = lambda helper: f"is_state('input_boolean.{slug}_{helper}', 'on')"  # noqa: E731
        power.append(f"({WATTS['light']} * (states('input_number.{slug}_light_brightness') | float(255)) / 255 "
                     f"if {on('light_power')} else 0)")
        power.append(f"({WATTS['ventilation']} if {on('ventilation_power')} else 0)")
        if full:
            power.append(f"({WATTS['socket']} if {on('socket_power')} else 0)")
            power.append(f"({WATTS['ac']} if {on('ac_compressor')} else 0)")
        if slug in HUMIDIFIERS:
            power.append(f"({WATTS['humidifier']} if {on('humidifier_power')} else 0)")

        # --- the physics of this room, one step a minute ---
        window_open = f"is_state('binary_sensor.{slug}_window', 'on')" if slug in WINDOWS else "false"
        drift = f"({DRIFT} * ({WINDOW_DRIFT} if {window_open} else 1))"
        t = f"states('input_number.{slug}_temperature_sim') | float({temperature})"
        heat = f"({HEATER_STEP} if is_state('input_boolean.{slug}_heater_power', 'on') else 0)"
        cool = f"({AC_STEP} if is_state('input_boolean.{slug}_ac_compressor', 'on') else 0)" if full else "0"
        physics.append({
            "action": "input_number.set_value",
            "target": {"entity_id": f"input_number.{slug}_temperature_sim"},
            "data": {"value": f"{{{{ ([[{t} + ({OUTSIDE} - {t}) * {drift} + {heat} - {cool}, 5] | max, 40] | min) | round(1) }}}}"},
        })
        c = f"states('input_number.{slug}_co2_sim') | float({co2})"
        vent = f"({CO2_VENTILATION} if is_state('input_boolean.{slug}_ventilation_power', 'on') else 0)"
        aired = f"({WINDOW_CO2} if {window_open} else 0)"
        physics.append({
            "action": "input_number.set_value",
            "target": {"entity_id": f"input_number.{slug}_co2_sim"},
            "data": {"value": f"{{{{ [[{c} + {CO2_BREATHING} - {vent} - {aired}, 400] | max, 5000] | min }}}}"},
        })
        h = f"states('input_number.{slug}_humidity_sim') | float({humidity})"
        wet = f"({HUMIDIFIER_STEP} if is_state('input_boolean.{slug}_humidifier_power', 'on') else 0)" \
            if slug in HUMIDIFIERS else "0"
        dry = f"({VENTILATION_DRYING} if is_state('input_boolean.{slug}_ventilation_power', 'on') else 0)"
        physics.append({
            "action": "input_number.set_value",
            "target": {"entity_id": f"input_number.{slug}_humidity_sim"},
            "data": {"value": f"{{{{ ([[{h} + ({OUTSIDE_HUMIDITY} - {h}) * {HUMIDITY_DRIFT} + {wet} - {dry}, 10] | max, 90] | min) | round(1) }}}}"},
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

    motion = [f"binary_sensor.{slug}_motion" for slug, _, _ in ROOMS]
    windows = [f"binary_sensor.{slug}_window" for slug in WINDOWS]
    openings = windows + [f"binary_sensor.{slug}_door" for slug in DOORS]
    armed = "input_boolean.security_armed"
    input_boolean["security_armed"] = {"name": "Охрана"}
    input_datetime["schedule_good_morning_time"] = {"name": "Расписание: время «Доброе утро»", "has_date": False,
                                                    "has_time": True}
    slug_of = {"slug": "{{ trigger.entity_id.split('.')[1].rsplit('_', 1)[0] }}"}

    def script_ran(script: str) -> list[dict]:
        return [{"trigger": "state", "entity_id": f"script.{script}", "to": "on"}]

    automations += [
        {
            "id": "window_pauses_climate",
            "alias": "Окно открыто: отопление и кондиционер на паузе, закрыто - как было",
            "mode": "parallel",
            "triggers": [{"trigger": "state", "entity_id": windows, "from": "off", "to": "on"},
                         {"trigger": "state", "entity_id": windows, "from": "on", "to": "off"}],
            "variables": slug_of,
            "actions": [{
                "choose": [{
                    "conditions": [{"condition": "template", "value_template": "{{ trigger.to_state.state == 'on' }}"}],
                    "sequence": [
                        {"action": "scene.create", "data": {"scene_id": "window_{{ slug }}", "snapshot_entities": [
                            "climate.{{ slug }}_heating", "climate.{{ slug }}_ac"]}},
                        {"action": "climate.set_hvac_mode", "target": {"entity_id": [
                            "climate.{{ slug }}_heating", "climate.{{ slug }}_ac"]}, "data": {"hvac_mode": "off"}},
                    ],
                }],
                # Closed: what was there before; a scene lost to a restart - heating back on.
                "default": [{
                    "choose": [{
                        "conditions": [{"condition": "template",
                                        "value_template": "{{ expand('scene.window_' ~ slug) | count > 0 }}"}],
                        "sequence": [{"action": "scene.turn_on", "target": {"entity_id": "scene.window_{{ slug }}"}}],
                    }],
                    "default": [{"action": "climate.set_hvac_mode", "target": {"entity_id": "climate.{{ slug }}_heating"},
                                 "data": {"hvac_mode": "heat"}}],
                }],
            }],
        },
        {
            "id": "night_light_on_motion",
            "alias": f"Ночью по движению: свет на {NIGHT_LIGHT_PCT}%",
            "mode": "parallel",
            "triggers": [{"trigger": "state", "entity_id": [f"binary_sensor.{s}_motion" for s in NIGHT_LIGHT_ROOMS],
                          "to": "on"}],
            "conditions": [{"condition": "time", "after": NIGHT[0], "before": NIGHT[1]},
                           {"condition": "state", "entity_id": armed, "state": "off"}],
            "variables": slug_of,
            "actions": [
                {"condition": "template", "value_template": "{{ is_state('light.' ~ slug, 'off') }}"},
                {"action": "light.turn_on", "target": {"entity_id": "light.{{ slug }}"},
                 "data": {"brightness_pct": NIGHT_LIGHT_PCT}},
                {"wait_template": "{{ is_state('binary_sensor.' ~ slug ~ '_motion', 'off') }}"},
                {"delay": "00:02:00"},
                {"condition": "template", "value_template": "{{ is_state('binary_sensor.' ~ slug ~ '_motion', 'off') }}"},
                {"action": "light.turn_off", "target": {"entity_id": "light.{{ slug }}"}},
            ],
        },
        {
            "id": "empty_room_light_off",
            "alias": f"Пустая комната: свет гаснет через {AUTO_OFF_MINUTES} минут без движения",
            "mode": "parallel",
            "triggers": [{"trigger": "state", "entity_id": motion, "to": "off", "for": {"minutes": AUTO_OFF_MINUTES}}],
            "variables": slug_of,
            "actions": [{"action": "light.turn_off", "target": {"entity_id": "light.{{ slug }}"}}],
        },
        {
            "id": "security_intrusion",
            "alias": "Охрана: движение или открытие в пустом доме",
            "mode": "parallel",
            "triggers": [{"trigger": "state", "entity_id": motion + openings, "to": "on"}],
            "conditions": [{"condition": "state", "entity_id": armed, "state": "on"}],
            "variables": slug_of,
            "actions": [
                {"delay": ENTRY_DELAY},  # time to come in and say "Я дома"
                {"condition": "state", "entity_id": armed, "state": "on"},
                {"action": "input_boolean.turn_on", "target": {"entity_id": "input_boolean.{{ slug }}_intrusion_flag"}},
            ],
        },
        {
            "id": "security_disarmed",
            "alias": "Охрана снята: тревоги сброшены",
            "triggers": [{"trigger": "state", "entity_id": armed, "to": "off"}],
            "actions": [{"action": "input_boolean.turn_off", "target": {"entity_id": [
                f"input_boolean.{slug}_intrusion_flag" for slug, _, _ in ROOMS]}}],
        },
        {
            "id": "security_arm_on_leaving",
            "alias": "Сценарий «Я ушёл»: охрана через 2 минуты",
            "mode": "restart",
            "triggers": script_ran("ya_ushel"),
            "actions": [
                {"wait_for_trigger": script_ran("ya_doma"), "timeout": ARM_DELAY, "continue_on_timeout": True},
                {"condition": "template", "value_template": "{{ wait.trigger is none }}"},
                {"action": "input_boolean.turn_on", "target": {"entity_id": armed}},
            ],
        },
        {
            "id": "security_disarm_on_coming_home",
            "alias": "Сценарий «Я дома»: охрана снята",
            "triggers": script_ran("ya_doma"),
            "actions": [{"action": "input_boolean.turn_off", "target": {"entity_id": armed}}],
        },
        {
            "id": "curtains_good_morning",
            "alias": "Сценарий «Доброе утро»: шторы открыть",
            "triggers": script_ran("dobroe_utro"),
            "actions": [{"action": "cover.open_cover", "target": {"entity_id": [
                f"cover.{slug}_curtains" for slug in CURTAINS]}}],
        },
        {
            "id": "curtains_good_night",
            "alias": "Сценарий «Спокойной ночи»: шторы закрыть",
            "triggers": script_ran("spokoynoy_nochi"),
            "actions": [{"action": "cover.close_cover", "target": {"entity_id": [
                f"cover.{slug}_curtains" for slug in CURTAINS]}}],
        },
        {
            "id": "schedule_good_morning",
            "alias": "Расписание: «Доброе утро» по будням",
            "triggers": [{"trigger": "time", "at": "input_datetime.schedule_good_morning_time"}],
            "conditions": [{"condition": "time", "weekday": ["mon", "tue", "wed", "thu", "fri"]},
                           {"condition": "state", "entity_id": armed, "state": "off"}],
            "actions": [{"action": "script.turn_on", "target": {"entity_id": "script.dobroe_utro"}}],
        },
        {
            "id": "schedule_sunset_entrance",
            "alias": "Расписание: свет в прихожей на закате",
            "triggers": [{"trigger": "sun", "event": "sunset"}],
            "conditions": [{"condition": "state", "entity_id": armed, "state": "off"}],
            "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.entrance"},
                         "data": {"brightness_pct": 60}}],
        },
    ]
    sensors.append({
        "name": "Дом: мощность", "unique_id": "house_power", "unit_of_measurement": "W",
        "device_class": "power", "state_class": "measurement",
        "state": "{{ (" + " + ".join(power) + ") | round(0) }}",
    })

    automations.append({
        "id": "virtual_house_physics",
        "alias": "Виртуальный дом: физика",
        "triggers": [{"trigger": "time_pattern", "minutes": "/1"}],
        "actions": physics,
    })

    config = {
        "default_config": None,
        "http": {"server_port": 8123},
        "recorder": {"purge_keep_days": 60},  # "какая температура была ночью", "сколько за месяц"
        "input_boolean": input_boolean,
        "input_number": input_number,
        "input_datetime": input_datetime,
        "template": [{"light": lights}, {"switch": switches}, {"fan": fans}, {"sensor": sensors},
                     {"binary_sensor": binary_sensors}, {"cover": covers}],
        "climate": climate,
        "generic_hygrostat": hygrostats,
        "sensor": [{"platform": "integration", "source": "sensor.house_power", "name": "Дом: электричество",
                    "unique_id": "house_energy", "unit_prefix": "k", "unit_time": "h", "round": 3,
                    "method": "left"}],
        "utility_meter": {
            "house_energy_today": {"source": "sensor.house_energy", "name": "Дом: электричество за сегодня",
                                   "unique_id": "house_energy_today", "cycle": "daily"},
            "house_energy_month": {"source": "sensor.house_energy", "name": "Дом: электричество за месяц",
                                   "unique_id": "house_energy_month", "cycle": "monthly"},
        },
        "automation": automations,
    }
    header = (
        "# GENERATED by homeassistant/virtual_house.py - edit ROOMS there, not this file.\n"
        "# The virtual house: real HA device types (light/switch/fan/climate/sensor/binary_sensor)\n"
        "# over helper entities, per-room norms the house keeps by itself, danger reflexes,\n"
        "# and a little physics.\n\n"
    )
    body = yaml.safe_dump(config, allow_unicode=True, sort_keys=False, width=200)
    # The scenarios: HA's script editor reads and writes this file.
    body += "script: !include scripts.yaml\n"
    return header + body.replace("default_config: null", "default_config:")


def _scenarios() -> str:
    """The default scenarios - written once, then they're the resident's."""
    lights = [f"light.{slug}" for slug, _, _ in ROOMS]
    sockets = [f"switch.{slug}_socket" for slug, _, full in ROOMS if full]
    acs = [f"climate.{slug}_ac" for slug, _, full in ROOMS if full]
    norms = [f"input_number.{slug}_temperature_norm" for slug, _, _ in ROOMS]
    bedroom = "input_number.bedroom_temperature_norm"

    def restore(scene: str, fallback: list[dict]) -> dict:
        # A scene saved by scene.create lives until Home Assistant restarts.
        return {"choose": [{"conditions": [{"condition": "template",
                                            "value_template": f"{{{{ states.scene.{scene} is not none }}}}"}],
                            "sequence": [{"action": "scene.turn_on", "target": {"entity_id": f"scene.{scene}"}}]}],
                "default": fallback}

    scripts = {
        "ya_ushel": {
            "alias": "Я ушёл",
            "description": "Фразы: я ушёл, я ушла, я ухожу, я пошёл, я пошла, меня не будет. Запоминает нормы и кондиционеры, "
                           f"гасит свет, выключает розетки и кондиционеры, держит {AWAY_TEMPERATURE} °C.",
            "sequence": [
                {"action": "scene.create", "data": {"scene_id": "before_leaving", "snapshot_entities": norms + acs}},
                {"action": "light.turn_off", "target": {"entity_id": lights}},
                {"action": "switch.turn_off", "target": {"entity_id": sockets}},
                {"action": "climate.set_hvac_mode", "target": {"entity_id": acs}, "data": {"hvac_mode": "off"}},
                {"action": "input_number.set_value", "target": {"entity_id": norms},
                 "data": {"value": AWAY_TEMPERATURE}},
            ],
        },
        "ya_doma": {
            "alias": "Я дома",
            "description": "Фразы: я дома, я пришёл, я пришла, я вернулся, я вернулась. Возвращает нормы и кондиционеры, как было "
                           "до ухода, включает свет в прихожей.",
            "sequence": [
                restore("before_leaving", [
                    {"action": "input_number.set_value", "target": {"entity_id": norms}, "data": {"value": 22}},
                    {"action": "climate.set_hvac_mode", "target": {"entity_id": acs}, "data": {"hvac_mode": "cool"}},
                ]),
                {"action": "light.turn_on", "target": {"entity_id": "light.entrance"}},
            ],
        },
        "spokoynoy_nochi": {
            "alias": "Спокойной ночи",
            "description": "Фразы: спокойной ночи, я спать, ложусь спать, отбой. Гасит свет везде, в спальне "
                           f"норма на {NIGHT_DROP} °C ниже до утра.",
            "sequence": [
                {"action": "scene.create", "data": {"scene_id": "before_night", "snapshot_entities": [bedroom]}},
                {"action": "light.turn_off", "target": {"entity_id": lights}},
                {"action": "input_number.set_value", "target": {"entity_id": bedroom},
                 "data": {"value": f"{{{{ [states('{bedroom}') | float(22) - {NIGHT_DROP}, 16] | max }}}}"}},
            ],
        },
        "dobroe_utro": {
            "alias": "Доброе утро",
            "description": "Фразы: доброе утро, я проснулся, я проснулась, подъём. Возвращает норму спальни, как было до "
                           "ночи, включает свет в спальне на 40%.",
            "sequence": [
                restore("before_night", []),
                {"action": "light.turn_on", "target": {"entity_id": "light.bedroom"}, "data": {"brightness_pct": 40}},
            ],
        },
    }
    return yaml.safe_dump(scripts, allow_unicode=True, sort_keys=False, width=200)


def write() -> None:
    CONFIG.write_text(_yaml(), encoding="utf-8")
    print(f"wrote {CONFIG}")
    if SCRIPTS.exists() and SCRIPTS.read_text(encoding="utf-8").strip():
        print(f"kept {SCRIPTS} (the scenarios are the resident's now)")
    else:
        SCRIPTS.write_text(_scenarios(), encoding="utf-8")
        print(f"wrote {SCRIPTS} (default scenarios)")


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
        wanted[("template", f"{slug}_motion")] = (f"binary_sensor.{slug}_motion", slug, False)
        wanted[("input_boolean", f"{slug}_motion_sim")] = (None, slug, False)
        wanted[("template", f"{slug}_intrusion")] = (f"binary_sensor.{slug}_intrusion", slug, False)
        wanted[("input_boolean", f"{slug}_intrusion_flag")] = (None, slug, True)
        for opening, rooms in (("window", WINDOWS), ("door", DOORS)):
            if slug in rooms:
                wanted[("template", f"{slug}_{opening}")] = (f"binary_sensor.{slug}_{opening}", slug, False)
                wanted[("input_boolean", f"{slug}_{opening}_sim")] = (None, slug, False)
        if slug in HUMIDIFIERS:
            wanted[("generic_hygrostat", f"{slug}_humidifier")] = (f"humidifier.{slug}_humidifier", slug, False)
            wanted[("input_number", f"{slug}_humidity_min")] = (None, slug, False)
            wanted[("input_boolean", f"{slug}_humidifier_power")] = (None, slug, True)
            wanted[("automation", f"{slug}_humidity_norm")] = (None, slug, False)
        if slug in CURTAINS:
            wanted[("template", f"{slug}_curtains")] = (f"cover.{slug}_curtains", slug, False)
            wanted[("input_number", f"{slug}_curtain_position")] = (None, slug, True)
        if full:
            wanted[("template", f"{slug}_socket")] = (f"switch.{slug}_socket", slug, False)
            wanted[("generic_thermostat", f"{slug}_ac")] = (f"climate.{slug}_ac", slug, False)
            wanted[("input_boolean", f"{slug}_socket_power")] = (None, slug, True)
            wanted[("input_boolean", f"{slug}_ac_compressor")] = (None, slug, True)
    wanted[("automation", "virtual_house_physics")] = (None, None, False)
    for valve in ("water_valve", "gas_valve"):
        wanted[("template", valve)] = (f"switch.{valve}", VALVE_ROOM, False)
        wanted[("input_boolean", f"{valve}_open")] = (None, VALVE_ROOM, True)
    for automation in ("danger_leak", "danger_gas", "danger_smoke", "window_pauses_climate", "night_light_on_motion",
                       "empty_room_light_off", "security_intrusion", "security_disarmed", "security_arm_on_leaving",
                       "security_disarm_on_coming_home", "curtains_good_morning", "curtains_good_night"):
        wanted[("automation", automation)] = (None, None, False)
    for schedule in ("schedule_good_morning", "schedule_sunset_entrance"):
        wanted[("automation", schedule)] = (f"automation.{schedule}", None, False)  # Jarvis finds them by id
    wanted[("input_datetime", "schedule_good_morning_time")] = (
        "input_datetime.schedule_good_morning_time", None, False)
    wanted[("input_boolean", "security_armed")] = ("input_boolean.security_armed", None, False)
    wanted[("template", "house_power")] = ("sensor.house_power", None, False)
    wanted[("integration", "house_energy")] = ("sensor.house_energy", None, False)
    # utility_meter adds its tariff to the unique id
    wanted[("utility_meter", "house_energy_today_single_tariff")] = ("sensor.house_energy_today", None, False)
    wanted[("utility_meter", "house_energy_month_single_tariff")] = ("sensor.house_energy_month", None, False)
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
        # A time helper without "initial" (that would reset it on every restart,
        # undoing "буди меня в 8") starts at midnight - that's "never set".
        for state in await call(type="get_states"):
            if state["entity_id"] == "input_datetime.schedule_good_morning_time" and state["state"] == "00:00:00":
                await call(type="call_service", domain="input_datetime", service="set_datetime",
                           service_data={"entity_id": state["entity_id"], "time": GOOD_MORNING_TIME})
                print(f"good morning time set to {GOOD_MORNING_TIME}")

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
