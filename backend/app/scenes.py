"""Scenarios made in the app: a name, the phrases that start it, and steps - turned into an
ordinary Home Assistant script (scripts.yaml), the same kind as the house's own, so Jarvis
runs it by voice (app.domains.home.run_scenario) and the resident can open it in HA.

A step is one device or one norm:
    {"room": "Спальня", "device": "light", "action": "off"}            # "room": "all" - everywhere
    {"room": "Спальня", "device": "light", "action": "on", "brightness_pct": 30}
    {"room": "Кухня", "device": "curtains", "action": "on", "position": 50}
    {"room": "Спальня", "norm": "temperature", "value": 20}
The steps ride along in the script's variables (jarvis_steps), so the app can show and edit
them again; Home Assistant ignores them.
"""

from __future__ import annotations

import re

from app.domains import home
from app.words import locative

NORM_KINDS = {"temperature": ("температура", "°C"), "co2_max": ("CO₂ до", " ppm"), "humidity_min": ("влажность от", "%")}
DEVICE_WORDS = {"light": "свет", "socket": "розетки", "ac": "кондиционер", "heating": "отопление",
                "ventilation": "вентиляцию", "humidifier": "увлажнитель", "curtains": "шторы",
                "water_valve": "кран воды", "gas_valve": "кран газа", "security": "охрану"}
ON_WORDS = {"curtains": ("открывает", "закрывает"), "security": ("ставит", "снимает"),
            "water_valve": ("открывает", "перекрывает"), "gas_valve": ("открывает", "перекрывает")}
LATIN = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                 ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "i", "k", "l", "m", "n", "o", "p", "r", "s", "t",
                  "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "iu", "ia"]))
MAX_STEPS = 30


def object_id(name: str, taken: set[str]) -> str:
    """'Кино вечером' -> 'kino_vecherom' (HA's script id), unique among the scripts."""
    base = re.sub(r"[^a-z0-9]+", "_", "".join(LATIN.get(c, c) for c in name.casefold())).strip("_") or "scenario"
    found, n = base, 2
    while found in taken:
        found, n = f"{base}_{n}", n + 1
    return found


def _phrases(said: list) -> list[str]:
    out = []
    for p in said or []:
        p = re.sub(r"[.,]", " ", str(p)).strip()  # the description keeps them as "Фразы: a, b."
        if p and p.casefold() not in {x.casefold() for x in out}:
            out.append(p[:60])
    return out


ICONS = ("play", "moon", "sunrise", "home", "exit", "light")  # the design's choice of badges


async def build(client, name: str, phrases: list, steps: list, icon: str = "play") -> tuple[dict, str]:
    """(the script's config, what it does in words). ValueError says what's wrong, in Russian."""
    name = str(name or "").strip()[:40]
    if not name:
        raise ValueError("нужно название")
    if not steps:
        raise ValueError("нужен хотя бы один шаг")
    if len(steps) > MAX_STEPS:
        raise ValueError(f"не больше {MAX_STEPS} шагов")
    rooms = await home._house(client)
    actions, does = [], []
    for step in steps:
        asked = str(step.get("room") or "").strip()
        everywhere = asked.casefold() in home.EVERYWHERE
        if "norm" in step:
            kind = step["norm"]
            if kind not in NORM_KINDS:
                raise ValueError("норма: temperature, co2_max или humidity_min")
            targets = list(rooms) if everywhere else [home.match_room(asked, list(rooms))]
            if targets == [None]:
                raise ValueError(f"нет комнаты «{asked}»")
            value = float(step.get("value"))
            for room in targets:
                for d in rooms[room]:
                    if d.get("type") == "norm" and d.get("kind") == kind:
                        low, high = d.get("min"), d.get("max")
                        if low is not None and high is not None and not low <= value <= high:
                            raise ValueError(f"норма в «{room}» может быть {low:g}–{high:g}")
                        actions.append({"action": "input_number.set_value", "target": {"entity_id": d["entity_id"]},
                                        "data": {"value": value}})
            word, unit = NORM_KINDS[kind]
            does.append(f"{word} {value:g}{unit} " + ("везде" if everywhere else locative(targets[0])))
            continue
        device, action = step.get("device"), step.get("action")
        if device not in home.DEVICE_TYPES or action not in ("on", "off"):
            raise ValueError("шаг: устройство и действие on/off")
        if device in home.VALVE_TYPES and action == "on":
            raise ValueError("открывать краны сценарием нельзя — только с подтверждением")
        if everywhere or device in home.HOUSE_TYPES:
            targets = [r for r, ds in rooms.items() if any(d.get("type") == device for d in ds)]
        else:
            room = home.match_room(asked, list(rooms))
            if room is None:
                raise ValueError(f"нет комнаты «{asked}»")
            targets = [room]
        entities = [d["entity_id"] for r in targets for d in rooms[r] if d.get("type") == device]
        if not entities:
            raise ValueError(f"нет устройства «{DEVICE_WORDS[device]}» " + ("в доме" if everywhere else locative(asked)))
        brightness = step.get("brightness_pct") if device == "light" and action == "on" else None
        position = step.get("position") if device == "curtains" else None
        temperature = step.get("temperature") if device in home.CLIMATE_TYPES and action == "on" else None
        domain, service, data = home.service_for(device, action, brightness, temperature, position)
        call = {"action": f"{domain}.{service}", "target": {"entity_id": entities}}
        if data:
            call["data"] = data
        actions.append(call)
        verb = ON_WORDS.get(device, ("включает", "выключает"))[0 if action == "on" else 1]
        where = "" if device in home.HOUSE_TYPES else (" везде" if everywhere else " " + locative(targets[0]))
        extra = f" на {brightness}%" if brightness else f" на {position}%" if position not in (None, 0, 100) else ""
        does.append(f"{verb} {DEVICE_WORDS[device]}{where}{extra}")
    text = ", ".join(does)
    text = text[:1].upper() + text[1:] + "."
    said = _phrases(phrases) or [name.casefold()]
    config = {"alias": name, "description": f"Фразы: {', '.join(said)}. {text}", "mode": "single",
              "sequence": actions,
              "variables": {"jarvis_steps": steps, "jarvis_icon": icon if icon in ICONS else "play"}}
    return config, text


# ---------------------------------------------------------------- schedules made in the app

SCHEDULE_PREFIX = "app_"  # their automation ids: these the app may rewrite and delete; the house's own it only switches


def schedule_config(name: str, time: str, days: list, scene_id: str) -> dict:
    """An automation in automations.yaml: at time, on these days (Mon=1), unless the house is guarded,
    runs the scenario. Its settings ride along in variables (jarvis_schedule) for the app to read back."""
    name = str(name or "").strip()[:40]
    if not name:
        raise ValueError("нужно название")
    try:
        hours, minutes = (int(x) for x in str(time).split(":")[:2])
    except ValueError:
        raise ValueError("время ЧЧ:ММ") from None
    if not (0 <= hours < 24 and 0 <= minutes < 60):
        raise ValueError("время ЧЧ:ММ")
    days = sorted({int(d) for d in days or [] if str(d) in {"1", "2", "3", "4", "5", "6", "7"}})
    if not days:
        raise ValueError("нужен хотя бы один день")
    if not re.fullmatch(r"[a-z0-9_]+", str(scene_id or "")):
        raise ValueError("какой сценарий запускать")
    at = f"{hours:02d}:{minutes:02d}"
    return {
        "alias": f"{home.SCHEDULE_PREFIX} {name}",
        "description": "Расписание из приложения Jarvis",
        "triggers": [{"trigger": "time", "at": f"{at}:00"}],
        "conditions": [
            {"condition": "template", "value_template": "{{ now().isoweekday() in %s }}" % days},
            {"condition": "state", "entity_id": "input_boolean.security_armed", "state": "off"},  # nobody home
        ],
        "actions": [{"action": "script.turn_on", "target": {"entity_id": f"script.{scene_id}"}}],
        "variables": {"jarvis_schedule": {"name": name, "time": at, "days": days, "scene": scene_id}},
        "mode": "single",
    }
