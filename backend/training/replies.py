"""Jarvis's spoken answers for training - built from the house's real results.

The teacher model's answers came out ungrammatical ("Свет выключена",
"Отопление включена", "включена в Прихожая, Зал") - a model trained on them
would speak like that. So answers are templates: genders and cases right,
several wordings each, and questions about the house answered by working
the numbers out from the status, so they're true by construction. In the
feminine, like Jarvis herself ("включила").
"""

import json
import random

from training.sim_house import ROOM_CATALOG

LOCATIVE = {
    "Кухня": "на кухне", "Спальня": "в спальне", "Зал": "в зале", "Гостиная": "в гостиной", "Кабинет": "в кабинете",
    "Коридор": "в коридоре", "Прихожая": "в прихожей", "Детская": "в детской", "Ванная": "в ванной",
    "Балкон": "на балконе", "Гостевая": "в гостевой", "Столовая": "в столовой",
}
# nominative, genitive, accusative, "on" state, "off" state, verb on, verb off
DEVICES = {
    "light": ("свет", "света", "свет", "включён", "выключен", "включила", "выключила"),
    "socket": ("розетка", "розетки", "розетку", "включена", "выключена", "включила", "выключила"),
    "ac": ("кондиционер", "кондиционера", "кондиционер", "включён", "выключен", "включила", "выключила"),
    "heating": ("отопление", "отопления", "отопление", "включено", "выключено", "включила", "выключила"),
    "ventilation": ("вентиляция", "вентиляции", "вентиляцию", "включена", "выключена", "включила", "выключила"),
    "water_valve": ("кран воды", "крана воды", "воду", "открыт", "перекрыт", "открыла", "перекрыла"),
    "gas_valve": ("кран газа", "крана газа", "газ", "открыт", "перекрыт", "открыла", "перекрыла"),
}
_NAMES = {name.casefold(): name for _, name in ROOM_CATALOG}


def room_name(asked: str) -> str:
    return _NAMES.get(asked.casefold(), asked.capitalize())


def at(room: str) -> str:
    """'Кухня' -> 'на кухне'."""
    return LOCATIVE.get(room_name(room), f"в комнате «{room}»")


def cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def number(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def degrees(x: float) -> str:
    if x != int(x):
        return f"{number(x)} градуса"
    n = int(x)
    m10, m100 = abs(n) % 10, abs(n) % 100
    word = "градус" if m10 == 1 and m100 != 11 else "градуса" if 2 <= m10 <= 4 and not 12 <= m100 <= 14 else "градусов"
    return f"{n} {word}"


def _float(value: str) -> float | None:
    try:
        return float(str(value).split()[0])
    except (ValueError, IndexError):
        return None


def _join(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " и " + parts[-1]


def _rooms_phrase(rooms: list[str]) -> str:
    return _join([at(r) for r in rooms])


# ------------------------------------------------------------------ control results


def _control_reply(rng: random.Random, pairs: list[tuple[dict, dict]]) -> str:
    sentences = []
    done_by_device: dict[tuple, list[str]] = {}
    extras = {}
    for c, result in pairs:
        args = c["arguments"]
        device, action = args.get("device"), args.get("action")
        if "done" in result:
            key = (device, action)
            done_by_device.setdefault(key, []).extend(d["room"] for d in result["done"])
            if result.get("brightness_pct") is not None:
                extras[key] = f" на {result['brightness_pct']}%"
            if result.get("temperature") is not None:
                extras[key] = f", {degrees(result['temperature'])}"
            continue
        sentences.append(_control_error(rng, args, result))
    for (device, action), rooms in done_by_device.items():
        nom, gen, acc, on_state, off_state, verb_on, verb_off = DEVICES[device]
        extra = extras.get((device, action), "")
        if device in ("water_valve", "gas_valve"):
            sentences.insert(0, cap(f"{verb_on if action == 'on' else verb_off} {acc}{extra}."))
            continue
        where = "во всём доме" if len(rooms) > 2 and pairs[0][0]["arguments"].get("room") == "all" else _rooms_phrase(rooms)
        state = on_state if action == "on" else off_state
        verb = verb_on if action == "on" else verb_off
        options = [f"{cap(verb)} {acc} {where}{extra}.", f"Готово, {nom} {where} {state}{extra}."]
        if rng.random() < 0.3:
            options.append(cap(f"{nom} {where} {state}{extra}."))
        # several devices at once: one way of saying it, not "Готово... Готово..."
        sentences.insert(0, options[0] if len(done_by_device) > 1 else rng.choice(options))
    return " ".join(sentences)


def _control_error(rng, args: dict, result: dict) -> str:
    error = result.get("error", "")
    device = args.get("device")
    nom, gen, acc, *_ = DEVICES.get(device, ("устройство", "устройства", "устройство", "", "", "", ""))
    verb_inf = {"on": "Включить", "off": "Выключить"}.get(args.get("action"), "Сделать")
    if device in ("water_valve", "gas_valve"):
        verb_inf = "Открыть" if args.get("action") == "on" else "Перекрыть"
    if error.startswith("No room called"):
        return f"Комнаты «{args.get('room')}» в доме нет."
    if " in any room" in error:
        return f"В доме нет {gen}."
    if error.startswith("There is no "):
        room = error.rsplit(" in ", 1)[-1].rstrip(".")
        return cap(f"{at(room)} нет {gen}.")
    if "more than one room" in error:
        rooms = result.get("rooms") or [args.get("room")]
        return f"{verb_inf} {acc} ещё и {_rooms_phrase(rooms)}? Это несколько комнат, подтверди."
    if "the house allows at all" in error:
        return f"{cap(degrees(args.get('temperature', 0)))} нельзя, дом держит от 5 до 35."
    if "outside the usual" in error:
        return f"{cap(degrees(args.get('temperature', 0)))} - это непривычно. Поставить?"
    if "can only be set to" in error:
        low, high = error.rsplit("to ", 1)[-1].replace(" °C.", "").split("-")
        return f"{cap(nom)} умеет только от {low} до {high} градусов."
    if "Gas is still detected" in error:
        return "Газ всё ещё обнаружен, кран газа не открою."
    if "a leak is still detected" in error:
        return "Протечка ещё не устранена. Всё равно открыть воду?"
    if "opening the gas always needs a yes" in error:
        return "Открыть газ? Подтверди."
    if "unreachable" in error or "refused" in error:
        return "Дом сейчас не отвечает, не получилось."
    return "Не получилось."


# ------------------------------------------------------------------ status answers

ON_KEYS = {"light": "свет", "socket": "розетка", "ventilation": "вентиляция", "ac": "кондиционер", "heating": "отопление"}


def _is_on(key: str, value) -> bool:
    text = str(value if not isinstance(value, list) else value[0])
    return text.startswith("on") or (key == "ac" and text.startswith("cool")) or (key == "heating" and "heating now" in text)


def _status_reply(rng, question: str | None, result: dict, asked_room: str | None) -> str:
    if "error" in result:
        return f"Комнаты «{asked_room}» в доме нет." if asked_room else "Дом сейчас не отвечает."
    rooms = result.get("rooms", {})
    if asked_room:
        name, data = next(iter(rooms.items()))
        where = at(name)
        if question == "temperature":
            t = _float(data.get("temperature", ""))
            return f"{cap(where)} {degrees(t)}." if t is not None else f"{cap(where)} нет датчика температуры."
        if question == "humidity":
            h = _float(data.get("humidity", ""))
            return f"Влажность {where} {h:g}%." if h is not None else f"{cap(where)} нет датчика влажности."
        if question == "co2":
            co2 = _float(data.get("carbon_dioxide", ""))
            limit = _float(data.get("norm", {}).get("co2_max", "")) or 800
            if co2 is None:
                return f"{cap(where)} нет датчика CO2."
            verdict = "душновато" if co2 > limit else "воздух в норме"
            return f"{cap(where)} CO2 {co2:g} ppm, {verdict}."
        if question == "norm":
            n = _float(data.get("norm", {}).get("temperature", ""))
            return f"Норма {where} {degrees(n)}." if n is not None else f"{cap(where)} нормы температуры нет."
        if question == "light":
            if "light" not in data:
                return f"{cap(where)} нет света."
            value = str(data["light"])
            if value.startswith("on"):
                pct = value.split()[-1] if "%" in value else ""
                return f"Да, свет {where} горит{f' на {pct}' if pct else ''}."
            return f"Нет, свет {where} выключен."
        if question == "ac":
            if "ac" not in data:
                return f"{cap(where)} нет кондиционера."
            value = str(data["ac"])
            if value.startswith("cool"):
                target = value.split("to ")[1].split(" °C")[0]
                return f"Да, кондиционер {where} охлаждает до {degrees(float(target))}."
            return f"Нет, кондиционер {where} выключен."
        on = [ON_KEYS[k] for k, v in data.items() if k in ON_KEYS and _is_on(k, v)]
        return f"{cap(where)} включены: {_join(on)}." if on else f"{cap(where)} ничего не включено."

    temps = {r: _float(d.get("temperature", "")) for r, d in rooms.items()}
    temps = {r: t for r, t in temps.items() if t is not None}
    if question in ("hottest", "coldest") and temps:
        room = (max if question == "hottest" else min)(temps, key=temps.get)
        word = "Теплее" if question == "hottest" else "Прохладнее"
        return f"{word} всего {at(room)}, {degrees(temps[room])}."
    if question == "all_temperatures" and temps:
        return cap("; ".join(f"{at(r)} {degrees(t)}" for r, t in temps.items()) + ".")
    if question == "stuffiest":
        co2 = {r: _float(d.get("carbon_dioxide", "")) for r, d in rooms.items()}
        co2 = {r: c for r, c in co2.items() if c is not None}
        if co2:
            room = max(co2, key=co2.get)
            return f"Душнее всего {at(room)}, CO2 {co2[room]:g} ppm."
    if question == "lights":
        lit = [r for r, d in rooms.items() if str(d.get("light", "")).startswith("on")]
        return f"Свет горит {_rooms_phrase(lit)}." if lit else "Свет нигде не горит."
    if question == "any_ac":
        cooling = [r for r, d in rooms.items() if str(d.get("ac", "")).startswith("cool")]
        return f"Кондиционер работает {_rooms_phrase(cooling)}." if cooling else "Кондиционеры нигде не работают."
    if question == "all_ok":
        dangers = [r for r, d in rooms.items() if "DETECTED" in json.dumps(d.get("danger_sensors", {}))]
        return f"Тревога {_rooms_phrase(dangers)}!" if dangers else "Всё спокойно, датчики опасности молчат."
    on_rooms = []
    for r, d in rooms.items():
        on = [ON_KEYS[k] for k, v in d.items() if k in ON_KEYS and _is_on(k, v)]
        if on:
            on_rooms.append(f"{at(r)} {_join(on)}")
    return cap("; ".join(on_rooms) + ".") if on_rooms else "Сейчас ничего не включено."


# ------------------------------------------------------------------ the rest


def _norm_reply(rng, pairs, direction: str | None) -> str:
    c, result = pairs[-1]
    if "error" in result:
        error = result["error"]
        if "norm can be" in error:
            return "Норму температуры можно ставить от 16 до 28 градусов."
        if "has no" in error:
            return f"{cap(at(c['arguments'].get('room', '')))} нет нормы, дом там её не держит."
        if error.startswith("No room called"):
            return f"Комнаты «{c['arguments'].get('room')}» в доме нет."
        return "Не получилось поменять норму."
    parts = []
    for d in result["done"]:
        if "temperature" in d:
            parts.append(f"{at(d['room'])} держу {degrees(d['temperature'])}")
        if "co2_max" in d:
            parts.append(f"CO2 {at(d['room'])} не выше {d['co2_max']:g} ppm")
    prefix = {"up": "Сделала теплее: ", "down": "Сделала прохладнее: "}.get(direction, rng.choice(["Хорошо, ", "Готово, "]))
    return prefix + _join(parts) + "."


def _scenario_reply(rng, result: dict) -> str:
    if "error" in result:
        return "Такого сценария нет."
    ran = result["ran"]
    return rng.choice([f"Запустила сценарий «{ran}».", f"Сценарий «{ran}» запущен.", f"Хорошо, «{ran}»."])


CHAT = {
    "thanks": ["Пожалуйста!", "Обращайся.", "Всегда рада помочь."],
    "abilities": ["Я включаю и выключаю свет, розетки, кондиционеры, отопление и вентиляцию, держу нужную температуру "
                  "и свежесть воздуха, запускаю сценарии и рассказываю, что происходит в доме."],
    "hello": ["Привет! Чем помочь?", "Здравствуй!", "Привет!"],
    "done": ["Хорошо.", "Поняла, если что - зови."],
}


def reply(rng: random.Random, intent, pairs: list[tuple[dict, dict]]) -> str:
    if intent.kind == "chat":
        return rng.choice(CHAT[intent.question])
    last_call, last_result = pairs[-1]
    name = last_call["name"]
    if name == "control_devices":
        return _control_reply(rng, pairs)
    if name == "set_room_norm":
        return _norm_reply(rng, pairs, intent.direction)
    if name == "run_scenario":
        return _scenario_reply(rng, last_result)
    asked = last_call["arguments"].get("room")
    if intent.direction and "error" not in last_result:  # "потеплее" in a room with no norm
        return f"{cap(at(asked))} нет нормы температуры, дом там её не держит."
    return _status_reply(rng, intent.question, last_result, asked)
