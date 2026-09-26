"""A random house for training data - served to Jarvis's real home tools.

Every training example gets its own house: its own rooms (3-7 out of a dozen
names), devices, readings and norms. The tools answering are the real ones
(app/domains/home.make_handlers) over a fake Home Assistant, so results and
errors have exactly the shape Jarvis sees in production. A model trained on
one fixed house would learn "the bedroom is at 22" instead of reading the
tool results - and the real house won't be the virtual one.
"""

import copy
import json
import random

# (slug, name) - the name is what Home Assistant calls the area.
ROOM_CATALOG = [
    ("kitchen", "Кухня"), ("bedroom", "Спальня"), ("living_room", "Зал"), ("lounge", "Гостиная"),
    ("office", "Кабинет"), ("corridor", "Коридор"), ("entrance", "Прихожая"), ("kids", "Детская"),
    ("bathroom", "Ванная"), ("balcony", "Балкон"), ("guest", "Гостевая"), ("dining", "Столовая"),
]
SCENARIOS = [
    ("ya_ushel", "Я ушёл", "я ушёл, я ушла, я ухожу, я пошёл, я пошла, меня не будет",
     "Запоминает нормы, гасит свет, выключает розетки и кондиционеры, держит 18 °C."),
    ("ya_doma", "Я дома", "я дома, я пришёл, я пришла, я вернулся, я вернулась",
     "Возвращает нормы и кондиционеры, включает свет в прихожей."),
    ("spokoynoy_nochi", "Спокойной ночи", "спокойной ночи, я спать, ложусь спать",
     "Гасит свет везде, в спальне норма на 2 °C ниже до утра."),
    ("dobroe_utro", "Доброе утро", "доброе утро, я проснулся, я проснулась, подъём",
     "Возвращает норму спальни, включает свет в спальне на 40%."),
    ("kino", "Кино", "включи кино, режим кино, смотрим фильм",
     "Приглушает свет в зале до 20%, выключает свет в остальных комнатах."),
]


def _state(entity_id, state, **attrs):
    return {"entity_id": entity_id, "state": state, "attributes": attrs}


class SimHouse:
    """What the home tools need from Home Assistant, over a generated house.
    call_service changes the state, so a later status sees an earlier action."""

    def __init__(self, rng: random.Random, rooms: int | None = None):
        self.rng = rng
        count = rooms or rng.randint(3, 7)
        chosen = rng.sample(ROOM_CATALOG, count)
        self.rooms = [name for _, name in chosen]
        self.states: list[dict] = []
        self.areas: dict[str, str | None] = {}
        self.scripts: dict[str, dict] = {}
        self.calls: list[tuple] = []
        for slug, name in chosen:
            self._room(slug, name)
        kitchen = next((slug for slug, name in chosen if name == "Кухня"), chosen[0][0])
        if rng.random() < 0.7:
            self._add(f"switch.water_valve", "on", kitchen, friendly_name="Главный кран воды")
            self._add(f"binary_sensor.{kitchen}_leak", "off", kitchen, device_class="moisture")
        if rng.random() < 0.5:
            self._add(f"switch.gas_valve", "on", kitchen, friendly_name="Главный кран газа")
            self._add(f"binary_sensor.{kitchen}_gas", "off", kitchen, device_class="gas")
        for object_id, alias, phrases, does in rng.sample(SCENARIOS, rng.randint(2, len(SCENARIOS))):
            self.states.append(_state(f"script.{object_id}", "off", friendly_name=alias))
            self.scripts[object_id] = {"alias": alias, "description": f"Фразы: {phrases}. {does}"}

    def _add(self, entity_id, state, slug_area, **attrs):
        self.states.append(_state(entity_id, state, **attrs))
        name = next((n for s, n in ROOM_CATALOG if s == slug_area), None)
        self.areas[entity_id] = name

    def _room(self, slug, name):
        r = self.rng
        temperature = round(r.uniform(18, 27), 1)
        if r.random() < 0.9:
            on = r.random() < 0.4
            attrs = {"friendly_name": f"{name}: свет"}
            if on:
                attrs["brightness"] = r.choice([255, 255, 204, 128, 77])
            self._add(f"light.{slug}", "on" if on else "off", slug, **attrs)
        if r.random() < 0.6:
            self._add(f"switch.{slug}_socket", r.choice(["on", "off", "off"]), slug, friendly_name=f"{name}: розетка")
        if r.random() < 0.6:
            self._add(f"fan.{slug}_ventilation", "off", slug, friendly_name=f"{name}: вентиляция")
        norm = r.choice([20, 21, 21.5, 22, 22, 22.5, 23, 24])
        if r.random() < 0.7:
            self._add(f"climate.{slug}_heating", "heat", slug, friendly_name=f"{name}: отопление", temperature=norm - 0.5,
                      current_temperature=temperature, hvac_action="heating" if temperature < norm - 0.8 else "idle",
                      min_temp=5, max_temp=30, hvac_modes=["heat", "off"])
        if r.random() < 0.5:
            mode = r.choice(["cool", "cool", "off"])
            self._add(f"climate.{slug}_ac", mode, slug, friendly_name=f"{name}: кондиционер", temperature=norm + 0.5,
                      current_temperature=temperature, hvac_action="cooling" if mode == "cool" and temperature > norm + 0.8 else "off",
                      min_temp=16, max_temp=30, hvac_modes=["cool", "off"])
        if r.random() < 0.85:
            for kind, value, unit, dc in (("temperature", temperature, "°C", "temperature"),
                                           ("humidity", r.randint(30, 65), "%", "humidity"),
                                           ("co2", r.randrange(450, 1400, 10), "ppm", "carbon_dioxide")):
                self._add(f"sensor.{slug}_{kind}", str(float(value)), slug, friendly_name=f"{name}: {kind}",
                          device_class=dc, unit_of_measurement=unit)
            self._add(f"input_number.{slug}_temperature_norm", str(float(norm)), slug, unit_of_measurement="°C",
                      min=16, max=28)
            self._add(f"input_number.{slug}_co2_max", str(float(r.choice([700, 800, 800, 900, 1000]))), slug,
                      unit_of_measurement="ppm", min=600, max=1500)
        if r.random() < 0.5:
            self._add(f"binary_sensor.{slug}_smoke", "off", slug, device_class="smoke")

    # --- what app.ha_client.HomeAssistantClient offers ---

    async def render_template(self, template):
        return json.dumps([[entity_id, area] for entity_id, area in self.areas.items()])

    async def get_states(self):
        return copy.deepcopy(self.states)

    async def get_script_config(self, object_id):
        return self.scripts[object_id]

    async def call_service(self, domain, service, entity_id=None, data=None):
        self.calls.append((domain, service, entity_id, dict(data or {})))
        state = next((s for s in self.states if s["entity_id"] == entity_id), None)
        if state is None:
            return []
        data = data or {}
        if service in ("turn_on", "turn_off"):
            state["state"] = "on" if service == "turn_on" else "off"
            if domain == "light" and data.get("brightness_pct"):
                state["attributes"]["brightness"] = round(data["brightness_pct"] * 255 / 100)
        elif service == "set_hvac_mode":
            state["state"] = data["hvac_mode"]
        elif service == "set_temperature":
            state["attributes"]["temperature"] = data["temperature"]
            state["state"] = data.get("hvac_mode", state["state"])
        elif service == "set_value":
            state["state"] = str(float(data["value"]))
        return []

    # --- for building examples ---

    def has(self, room: str, device: str) -> bool:
        suffix = {"light": "light.", "socket": "_socket", "ventilation": "_ventilation", "heating": "_heating",
                  "ac": "_ac"}[device]
        return any(area == room and (e.startswith(suffix) if suffix.endswith(".") else e.endswith(suffix))
                   for e, area in self.areas.items())

    def norm(self, room: str) -> float | None:
        entity = next((e for e, a in self.areas.items() if a == room and e.endswith("_temperature_norm")), None)
        state = next((s for s in self.states if s["entity_id"] == entity), None)
        return float(state["state"]) if state else None
