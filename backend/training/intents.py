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
from datetime import datetime, timedelta
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
    required_any: tuple[str, ...] = ()  # the phrasing has to have one of these stems
    no_commands: bool = False  # a question or a complaint - no "включи"/"погаси" in it
    forbidden_any: tuple[str, ...] = ()  # stems that would mean something else ("потеплее" for "потемнее")
    # Calls that depend on the clock the example is set at ("вчера", "ночью") - instead of calls.
    calls_at: Callable[[datetime], list[dict]] | None = None


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
        return Intent("status", f"человек спрашивает: {what}; комната: {room.lower()}", [stem(room)],
                      [call("get_home_status", room=_room(room))], question=question, no_commands=True)
    question, what = rng.choice([("hottest", "где в доме жарче всего"), ("coldest", "где холоднее всего"),
                                 ("on", "что сейчас включено в доме"), ("lights", "где горит свет"),
                                 ("all_temperatures", "какая температура во всех комнатах"),
                                 ("stuffiest", "где душнее всего"), ("all_ok", "всё ли в доме в порядке"),
                                 ("any_ac", "работает ли где-нибудь кондиционер")])
    return Intent("status", f"человек спрашивает про весь дом: {what}", [], [call("get_home_status")],
                  question=question, no_commands=True)


def _norm_absolute(rng, house) -> Intent:
    room = rng.choice(house.rooms)
    if rng.random() < 0.75:
        value = rng.choice([18, 19, 20, 20.5, 21, 21.5, 22, 22.5, 23, 24, 25])
        shown = f"{value:g}"
        return Intent("norm", f"человек просит держать температуру {shown} градусов; комната: {room.lower()}",
                      [stem(room), shown], [call("set_room_norm", room=_room(room), temperature=value)],
                      no_commands=True)
    value = rng.choice([700, 800, 900, 1000])
    return Intent("norm", f"человек просит держать CO2 не выше {value}; комната: {room.lower()}",
                  [stem(room), str(value)], [call("set_room_norm", room=_room(room), co2_max=value)], no_commands=True)


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
    meaning = f"человек просит сделать {'теплее' if warmer else 'прохладнее'} (без числа); комната: {room.lower()}"
    return Intent("norm_relative", meaning, [stem(room)], [call("get_home_status", room=_room(room))],
                  then=_relative(room, 1 if warmer else -1), direction="up" if warmer else "down",
                  required_any=("тепл",) if warmer else ("прохлад", "холод", "охлад"), no_commands=True)


BRIGHTNESS_STEP = 30


def _brightness_step(room: str, dimmer: bool):
    def then(house: SimHouse):
        light = house.light(room)
        if light is None:
            return None  # no light there - the answer says so
        on, pct = light
        args = {"room": _room(room), "device": "light", "action": "on"}
        if dimmer:
            if not on or pct <= 10:
                return None  # already off / at the least
            return [call("control_devices", **args, brightness_pct=max(10, pct - BRIGHTNESS_STEP))]
        if not on:
            return [call("control_devices", **args)]
        if pct >= 100:
            return None  # already full
        return [call("control_devices", **args, brightness_pct=min(100, pct + BRIGHTNESS_STEP))]
    return then


def _brightness_relative(rng, house) -> Intent:
    """"Сделай потемнее" once went to the model as "потеплее" - it had never
    seen dimming. The house tells the brightness, a step of 30% is taken.
    Mostly a room where the light is on - that's when people ask, and a
    random room left real steps at 103 of 507 examples."""
    lit = [r for r in house.rooms if (light := house.light(r)) and light[0]]
    room = rng.choice(lit) if lit and rng.random() < 0.75 else rng.choice(house.rooms)
    dimmer = rng.random() < 0.5
    meaning = (f"человек просит сделать свет {'потемнее, приглушить' if dimmer else 'посветлее, ярче'} "
               f"(без числа); комната: {room.lower()}")
    return Intent("brightness", meaning, [stem(room)], [call("get_home_status", room=_room(room))],
                  then=_brightness_step(room, dimmer), direction="down" if dimmer else "up",
                  required_any=("темн", "тускл", "приглуш", "убав") if dimmer else ("светл", "ярч", "ярк", "прибав"),
                  forbidden_any=(("светл", "ярч", "ярк") if dimmer else ("темн", "тускл", "приглуш"))
                  + ("тепл", "холод", "прохлад", "жар"),
                  no_commands=True)


def _complaint(rng, house) -> Intent:
    room = rng.choice(house.rooms)
    kind = rng.choice(["stuffy", "cold", "hot"])
    if kind == "stuffy":
        return Intent("complaint", f"человек жалуется, что душно (просто жалоба, без просьбы); комната: {room.lower()}",
                      [stem(room)], [call("control_devices", room=_room(room), device="ventilation", action="on")],
                      required_any=("душн", "дышать", "сперт", "нечем"), no_commands=True)
    return Intent("complaint", f"человек жалуется, что {'холодно' if kind == 'cold' else 'жарко'} (просто жалоба, "
                  f"без просьбы и чисел); комната: {room.lower()}", [stem(room)],
                  [call("get_home_status", room=_room(room))],
                  then=_relative(room, 1 if kind == "cold" else -1), direction="up" if kind == "cold" else "down",
                  required_any=("холод", "мерз", "зябк", "дубак") if kind == "cold" else ("жарк", "жара", "парит", "пекло"),
                  no_commands=True)


def _scenario(rng, house) -> Intent:
    object_id = rng.choice(list(house.scripts))
    alias = house.scripts[object_id]["alias"]
    phrases = house.scripts[object_id]["description"].split(".")[0].removeprefix("Фразы: ")
    return Intent("scenario", f"человек говорит что-то вроде: {phrases} (сценарий «{alias}»)", [],
                  [call("run_scenario", name="?")], name_from_phrase=True)


def _valve(rng, house) -> Intent:
    which = rng.choice(["water_valve", "gas_valve"])
    action = rng.choices(["off", "on"], [3, 1])[0]
    return Intent("valve", f"{'ОТКРЫТЬ' if action == 'on' else 'ПЕРЕКРЫТЬ'} {DEVICE_WORDS[which]}", [],
                  [call("control_devices", room="all", device=which, action=action)], ask_again_on_confirm=True,
                  action="open" if action == "on" else "close",
                  required_any=("газ",) if which == "gas_valve" else ())  # a bare "главный кран" is water


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


# --- the second batch of the house: curtains, humidifiers, the guard, history, schedules ---

CURTAIN_WORDS = ("штор", "жалюз", "занавес", "портьер")
ARM_WORDS = ("постав", "включи охран", "охрану включ", "на охран")
DISARM_WORDS = ("сними", "сня", "выключи охран", "отключи охран", "убери охран")


def _rooms_with(house: SimHouse, device: str) -> list[str]:
    return [r for r in house.rooms if house.has(r, device)]


def _curtains(rng, house) -> Intent:
    rooms = _rooms_with(house, "curtains")
    everywhere = not rooms or rng.random() < 0.2
    room = None if everywhere else rng.choice(rooms)
    where = "ВО ВСЕХ комнатах (все шторы)" if everywhere else f"комната: {room.lower()}"
    if not everywhere and rng.random() < 0.3:
        position = rng.choice([30, 50, 50, 70])
        said = "наполовину" if position == 50 else f"на {position}%"
        mention = [stem(room)] + ([] if position == 50 else [str(position)])
        return Intent("curtains", f"ОТКРЫТЬ шторы {said}; {where}", mention,
                      [call("control_devices", room=_room(room), device="curtains", action="on", position=position)],
                      required_any=CURTAIN_WORDS + ("жалюзи",) if position != 50 else ("наполовин", "половин"))
    action = rng.choice(["on", "off"])
    word = "ОТКРЫТЬ" if action == "on" else "ЗАКРЫТЬ"
    return Intent("curtains", f"{word} шторы; {where}", [] if everywhere else [stem(room)],
                  [call("control_devices", room="all" if everywhere else _room(room), device="curtains",
                        action=action)],
                  action="open" if action == "on" else "close", required_any=CURTAIN_WORDS)


def _humidifier(rng, house) -> Intent:
    rooms = _rooms_with(house, "humidifier")
    room = rng.choice(rooms) if rooms else rng.choice(house.rooms)
    if rng.random() < 0.5:
        value = rng.choice([35, 40, 45, 50, 55])
        return Intent("humidifier", f"человек просит держать влажность не ниже {value}%; комната: {room.lower()}",
                      [stem(room), str(value)], [call("set_room_norm", room=_room(room), humidity_min=value)],
                      required_any=("влажн", "увлажн"), no_commands=True)
    action = rng.choice(["on", "off"])
    return Intent("humidifier", f"{ON_OFF[action]} увлажнитель; комната: {room.lower()}", [stem(room)],
                  [call("control_devices", room=_room(room), device="humidifier", action=action)],
                  action=action, required_any=("увлажн",))


def _security(rng, house) -> Intent:
    arm = rng.random() < 0.5
    meaning = "ПОСТАВИТЬ дом на охрану" if arm else "СНЯТЬ дом с охраны"
    return Intent("security", meaning, [], [call("control_devices", room="all", device="security",
                                                 action="on" if arm else "off")],
                  required_any=ARM_WORDS if arm else DISARM_WORDS,
                  forbidden_any=DISARM_WORDS if arm else ("постав",))


def _house_status(rng, house) -> Intent:
    """Questions the house answers now - windows, movement, curtains, the guard, power."""
    choices = []
    with_window = _rooms_with(house, "window")
    if with_window:
        choices.append(("window", "открыто ли там окно", rng.choice(with_window)))
    choices.append(("movement", "есть ли там сейчас кто-нибудь (движение)", rng.choice(house.rooms)))
    with_curtains = _rooms_with(house, "curtains")
    if with_curtains:
        choices.append(("curtains", "открыты ли там шторы", rng.choice(with_curtains)))
    with_humidifier = _rooms_with(house, "humidifier")
    if with_humidifier:
        choices.append(("humidifier", "работает ли там увлажнитель", rng.choice(with_humidifier)))
    choices += [("security", "стоит ли дом на охране", None), ("power", "сколько электричества дом тратит прямо сейчас", None),
                ("windows", "где в доме открыты окна", None)]
    question, what, room = rng.choice(choices)
    if room is None:
        return Intent("house_status", f"человек спрашивает: {what}", [], [call("get_home_status")],
                      question=question, no_commands=True)
    return Intent("house_status", f"человек спрашивает: {what}; комната: {room.lower()}", [stem(room)],
                  [call("get_home_status", room=_room(room))], question=question, no_commands=True)


def _period(rng, electricity: bool):
    """(what the person says, start and end from the clock) - worked out here, not by a model."""
    def day(when: datetime, days_back: int = 0) -> datetime:
        return when.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_back)

    fmt = lambda t: t.strftime("%Y-%m-%dT%H:%M")  # noqa: E731
    if electricity:
        options = [
            ("за сегодня", lambda w: (day(w), w)),
            ("вчера", lambda w: (day(w, 1), day(w))),
            ("за эту неделю", lambda w: (day(w, w.weekday()), w)),
            ("за этот месяц", lambda w: (day(w).replace(day=1), w)),
            ("за прошлый месяц", lambda w: ((day(w).replace(day=1) - timedelta(days=1)).replace(day=1),
                                            day(w).replace(day=1))),
        ]
    else:
        options = [
            ("за последний час", lambda w: (w - timedelta(hours=1), w)),
            ("за последние 3 часа", lambda w: (w - timedelta(hours=3), w)),
            ("этой ночью", lambda w: (day(w), day(w) + timedelta(hours=7))
                if w.hour >= 8 else (day(w, 1), day(w, 1) + timedelta(hours=7))),
            ("вчера", lambda w: (day(w, 1), day(w))),
            ("за сутки", lambda w: (w - timedelta(hours=24), w)),
        ]
    said, span = rng.choice(options)
    numbers = [n for n in said.split() if n.isdigit()]  # "за последние 3 часа" - the 3 has to be said
    return said, numbers, lambda when: tuple(fmt(t) for t in span(when))


def _history(rng, house) -> Intent:
    electricity = rng.random() < 0.3
    said, numbers, span = _period(rng, electricity)
    if electricity:
        def calls(when):
            start, end = span(when)
            return [call("home_history", what="electricity", start=start, end=end)]
        return Intent("history", f"человек спрашивает, сколько электричества дом потратил {said}", numbers, [],
                      calls_at=calls, question="electricity", no_commands=True,
                      required_any=("электр", "энерг", "свет", "квт", "кило"))
    room = rng.choice(house.rooms)
    what, word = rng.choice([("temperature", "какая была температура"), ("humidity", "какая была влажность"),
                             ("co2", "какой был CO2 (духота)")])

    def calls(when):
        start, end = span(when)
        return [call("home_history", what=what, room=_room(room), start=start, end=end)]
    required = {"temperature": ("темпер", "градус", "тепл", "холод"), "humidity": ("влажн",),
                "co2": ("co2", "углекисл", "душн", "воздух")}[what]
    return Intent("history", f"человек спрашивает, {word} {said}; комната: {room.lower()}", [stem(room)] + numbers, [],
                  calls_at=calls, question=what, no_commands=True, required_any=required)


SCHEDULE_NAMES = {"schedule_good_morning": "доброе утро", "schedule_sunset_entrance": "свет на закате",
                  "schedule_good_night": "спокойной ночи"}


def _schedule(rng, house) -> Intent:
    present = [s["entity_id"].split(".", 1)[1] for s in house.states if s["entity_id"].startswith("automation.schedule_")]
    if not present or rng.random() < 0.25:
        return Intent("schedule", "человек спрашивает, какие в доме расписания и во сколько они срабатывают", [],
                      [call("house_schedule", action="list")], question="list", no_commands=True,
                      required_any=("распис", "во сколько", "когда"))
    object_id = rng.choice(present)
    name = SCHEDULE_NAMES[object_id]
    timed = object_id != "schedule_sunset_entrance"
    if timed and rng.random() < 0.6:
        hour, minute = rng.choice([(6, 0), (6, 30), (7, 0), (7, 30), (8, 0), (8, 30), (9, 0)] if object_id ==
                                  "schedule_good_morning" else [(22, 0), (22, 30), (23, 0), (23, 30)])
        at = f"{hour:02d}:{minute:02d}"
        mention = [str(hour)] + ([f"{minute:02d}"] if minute else [])
        return Intent("schedule", f"перенести расписание «{name}» на {hour}:{minute:02d}", mention,
                      [call("house_schedule", action="set_time", name=name, time=at)], question="set_time")
    action = rng.choice(["enable", "disable"])
    word = "ВКЛЮЧИТЬ" if action == "enable" else "ВЫКЛЮЧИТЬ (больше не нужно)"
    return Intent("schedule", f"{word} расписание «{name}»", [], [call("house_schedule", action=action, name=name)],
                  question=action, action="on" if action == "enable" else "off")


# Kinds added after the main set was built: drawn only when asked for by
# name (build_dataset --kinds), so the main set's draws - and the teacher's
# cached phrasings for them - stay the same.
EXTRA_KINDS: dict[str, Callable] = {
    "brightness": _brightness_relative, "curtains": _curtains, "humidifier": _humidifier, "security": _security,
    "house_status": _house_status, "history": _history, "schedule": _schedule,
}


def draw(rng: random.Random, house: SimHouse, kinds: list[str] | None = None) -> Intent:
    if kinds:
        return EXTRA_KINDS[rng.choice(kinds)](rng, house)
    make = rng.choices([k for k, _ in KINDS], [w for _, w in KINDS])[0]
    return make(rng, house)
