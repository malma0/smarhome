"""What a training example is about - with the right tool calls worked out
by this code, not by a model.

The big models aren't reliable enough to label with: asked to turn the
light off "в спальне и на кухне", gpt-oss-120b and gpt-oss-20b turned it off
in the bedroom only, qwen3.8-27b in the whole house. So an intent is drawn
first ("light off: bedroom + kitchen") together with its correct calls (one
per room); a teacher model only writes how a person would say it.
"""

import random
from dataclasses import dataclass, field
from typing import Callable

from training.sim_house import ROOM_CATALOG, SimHouse

DEVICE_WORDS = {
    "light": "свет", "socket": "розетку", "ac": "кондиционер", "heating": "отопление",
    "ventilation": "вентиляцию", "water_valve": "воду (главный кран воды)", "gas_valve": "газ (главный кран газа)",
}
ON_OFF = {"on": "ВКЛЮЧИТЬ", "off": "ВЫКЛЮЧИТЬ"}


def stem(word: str) -> str:
    word = word.casefold().replace("ё", "е")
    return word[:4] if len(word) > 4 else word[:3]


@dataclass
class Intent:
    kind: str
    meaning: str  # for the teacher: exactly what the person wants, in Russian
    must_mention: list[str] = field(default_factory=list)  # stems/numbers a phrasing has to contain
    calls: list[dict] = field(default_factory=list)  # the assistant's first tool calls
    then: Callable[[SimHouse], list[dict] | None] | None = None  # the next step, from the house's answer
    name_from_phrase: bool = False  # run_scenario gets what was said
    ask_again_on_confirm: bool = False  # a "confirm?" result gets a "да" turn
    action: str | None = None  # "on"/"off" - the phrasing has to say it, not the opposite
    question: str | None = None  # status / chat: what's asked, for the answer (training/replies.py)
    direction: str | None = None  # "up"/"down" for "потеплее"/"прохладнее"


def call(tool: str, **arguments) -> dict:
    return {"name": tool, "arguments": {k: v for k, v in arguments.items() if v is not None}}


def _room(name: str) -> str:
    return name.casefold()


def _other_room(rng: random.Random, house: SimHouse) -> str:
    """A room this house doesn't have - "в детской" when there's no nursery."""
    missing = [n for _, n in ROOM_CATALOG if n not in house.rooms]
    return rng.choice(missing) if missing else rng.choice(house.rooms)


def _control(rng, house) -> Intent:
    room = rng.choice(house.rooms) if rng.random() < 0.93 else _other_room(rng, house)
    device = rng.choices(["light", "socket", "ac", "heating", "ventilation"], [5, 2, 2, 1, 2])[0]
    action = rng.choice(["on", "off"])
    args, extra, mention = {}, "", [stem(room)]
    if device == "light" and action == "on" and rng.random() < 0.35:
        args["brightness_pct"] = rng.choice([10, 20, 30, 40, 50, 60, 70, 80, 100])
        extra = f" на {args['brightness_pct']}%"
        mention.append(str(args["brightness_pct"]))
    if device in ("ac", "heating") and action == "on" and rng.random() < 0.45:
        args["temperature"] = rng.choice([18, 19, 20, 21, 22, 23, 24, 25, 26] + [14, 30])
        extra = f" на {args['temperature']} градусов"
        mention.append(str(args["temperature"]))
    meaning = f"{ON_OFF[action]} {DEVICE_WORDS[device]}{extra}; комната: {room.lower()}"
    return Intent("control", meaning, mention, [call("control_devices", room=_room(room), device=device,
                                                     action=action, **args)], ask_again_on_confirm=True,
                  action=action)


def _multi_room(rng, house) -> Intent:
    rooms = rng.sample(house.rooms, min(len(house.rooms), rng.choice([2, 2, 3])))
    device = rng.choices(["light", "socket", "ventilation", "ac"], [6, 1, 2, 1])[0]
    action = rng.choice(["on", "off"])
    names = ", ".join(r.lower() for r in rooms)
    return Intent(
        "multi_room", f"{ON_OFF[action]} {DEVICE_WORDS[device]} в нескольких комнатах: {names} (все названы)",
        [stem(r) for r in rooms],
        [call("control_devices", room=_room(r), device=device, action=action) for r in rooms],
        ask_again_on_confirm=True, action=action,
    )


def _whole_house(rng, house) -> Intent:
    device = rng.choices(["light", "ventilation", "socket", "ac"], [7, 2, 1, 1])[0]
    action = rng.choice(["on", "off", "off"])
    return Intent("whole_house", f"{ON_OFF[action]} {DEVICE_WORDS[device]} ВО ВСЁМ ДОМЕ (везде)", [],
                  [call("control_devices", room="all", device=device, action=action)], ask_again_on_confirm=True,
                  action=action)


def _multi_device(rng, house) -> Intent:
    room = rng.choice(house.rooms)
    devices = rng.sample(["light", "socket", "ventilation", "ac"], 2)
    action = rng.choice(["on", "off"])
    words = " и ".join(DEVICE_WORDS[d] for d in devices)
    return Intent("multi_device", f"{ON_OFF[action]} сразу {words}; комната: {room.lower()}", [stem(room)],
                  [call("control_devices", room=_room(room), device=d, action=action) for d in devices],
                  ask_again_on_confirm=True, action=action)


def _status(rng, house) -> Intent:
    if rng.random() < 0.5:
        room = rng.choice(house.rooms)
        question, what = rng.choice([("temperature", "какая там температура"), ("humidity", "какая там влажность"),
                                     ("co2", "душно ли там (CO2)"), ("on", "что там включено"),
                                     ("light", "горит ли там свет"), ("ac", "работает ли там кондиционер"),
                                     ("norm", "какая там норма температуры")])
        return Intent("status", f"СПРОСИТЬ: {what}; комната: {room.lower()}", [stem(room)],
                      [call("get_home_status", room=_room(room))], question=question)
    question, what = rng.choice([("hottest", "где в доме жарче всего"), ("coldest", "где холоднее всего"),
                                 ("on", "что сейчас включено в доме"), ("lights", "где горит свет"),
                                 ("all_temperatures", "какая температура во всех комнатах"),
                                 ("stuffiest", "где душнее всего"), ("all_ok", "всё ли в доме в порядке"),
                                 ("any_ac", "работает ли где-нибудь кондиционер")])
    return Intent("status", f"СПРОСИТЬ про весь дом: {what}", [], [call("get_home_status")], question=question)


def _norm_absolute(rng, house) -> Intent:
    room = rng.choice(house.rooms)
    if rng.random() < 0.75:
        value = rng.choice([18, 19, 20, 20.5, 21, 21.5, 22, 22.5, 23, 24, 25])
        shown = f"{value:g}"
        return Intent("norm", f"ДЕРЖАТЬ температуру {shown} градусов (новая норма); комната: {room.lower()}",
                      [stem(room), shown.split(".")[0]], [call("set_room_norm", room=_room(room), temperature=value)])
    value = rng.choice([700, 800, 900, 1000])
    return Intent("norm", f"НОРМА CO2 не выше {value} ppm; комната: {room.lower()}", [stem(room), str(value)],
                  [call("set_room_norm", room=_room(room), co2_max=value)])


def _relative(room: str, delta: float):
    def then(house: SimHouse):
        current = house.norm(room)
        if current is None:
            return None  # no norm there - the answer says so
        return [call("set_room_norm", room=_room(room), temperature=max(16, min(28, current + delta)))]
    return then


def _norm_relative(rng, house) -> Intent:
    room = rng.choice(house.rooms)
    warmer = rng.random() < 0.5
    meaning = f"сделать {'ТЕПЛЕЕ' if warmer else 'ПРОХЛАДНЕЕ'} (без числа); комната: {room.lower()}"
    return Intent("norm_relative", meaning, [stem(room)], [call("get_home_status", room=_room(room))],
                  then=_relative(room, 1 if warmer else -1), direction="up" if warmer else "down")


def _complaint(rng, house) -> Intent:
    room = rng.choice(house.rooms)
    kind = rng.choice(["stuffy", "cold", "hot"])
    if kind == "stuffy":
        return Intent("complaint", f"ПОЖАЛОВАТЬСЯ, что душно (без просьбы); комната: {room.lower()}", [stem(room)],
                      [call("control_devices", room=_room(room), device="ventilation", action="on")])  # a complaint, no "включи"
    return Intent("complaint", f"ПОЖАЛОВАТЬСЯ, что {'холодно' if kind == 'cold' else 'жарко'} (без просьбы и чисел); "
                  f"комната: {room.lower()}", [stem(room)], [call("get_home_status", room=_room(room))],
                  then=_relative(room, 1 if kind == "cold" else -1), direction="up" if kind == "cold" else "down")


def _scenario(rng, house) -> Intent:
    object_id = rng.choice(list(house.scripts))
    alias = house.scripts[object_id]["alias"]
    phrases = house.scripts[object_id]["description"].split(".")[0].removeprefix("Фразы: ")
    return Intent("scenario", f"СЦЕНАРИЙ «{alias}» - сказать что-то вроде: {phrases}", [],
                  [call("run_scenario", name="?")], name_from_phrase=True)


def _valve(rng, house) -> Intent:
    which = rng.choice(["water_valve", "gas_valve"])
    action = rng.choices(["off", "on"], [3, 1])[0]
    return Intent("valve", f"{'ОТКРЫТЬ' if action == 'on' else 'ПЕРЕКРЫТЬ'} {DEVICE_WORDS[which]}", [],
                  [call("control_devices", room="all", device=which, action=action)], ask_again_on_confirm=True,
                  action="open" if action == "on" else "close")


def _chat(rng, house) -> Intent:
    question, what = rng.choice([("thanks", "поблагодарить Джарвис"),
                                 ("abilities", "спросить, что Джарвис умеет делать по дому"),
                                 ("hello", "поздороваться с Джарвис"),
                                 ("done", "сказать, что всё, больше ничего не нужно")])
    return Intent("chat", what, [], [], question=question)


KINDS: list[tuple[Callable, int]] = [
    (_control, 30), (_multi_room, 13), (_whole_house, 6), (_multi_device, 6), (_status, 13), (_norm_absolute, 8),
    (_norm_relative, 7), (_complaint, 6), (_scenario, 5), (_valve, 3), (_chat, 3),
]


def draw(rng: random.Random, house: SimHouse) -> Intent:
    make = rng.choices([k for k, _ in KINDS], [w for _, w in KINDS])[0]
    return make(rng, house)
