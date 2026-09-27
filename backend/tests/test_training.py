"""The training-data machinery (backend/training) - no network, no GPU."""

import asyncio
import json
import random

from training import replies
from training.build_dataset import build_conversation, expected_effects, phrase_ok
from training.intents import Intent, _multi_room, _norm_relative, call
from training.sim_house import SimHouse
from training.train_lora import encode


def test_a_house_is_the_same_for_the_same_seed_and_the_real_tools_run_on_it():
    a, b = SimHouse(random.Random(42)), SimHouse(random.Random(42))
    assert a.rooms == b.rooms and a.states == b.states
    from app.domains import home
    from app.tools.registry import TurnContext

    status, *_ = home.make_handlers(a)
    rooms = asyncio.run(status({}, TurnContext()))["rooms"]
    assert set(rooms) <= set(a.rooms)


def test_several_rooms_get_one_call_each():
    rng = random.Random(3)
    house = SimHouse(random.Random(9), rooms=5)
    intent = _multi_room(rng, house)
    rooms = [c["arguments"]["room"] for c in intent.calls]
    assert len(rooms) >= 2 and len(set(rooms)) == len(rooms)
    assert {c["arguments"]["device"] for c in intent.calls} == {intent.calls[0]["arguments"]["device"]}


def test_warmer_reads_the_norm_then_raises_it_by_one():
    house = SimHouse(random.Random(5), rooms=6)
    rng = random.Random(1)
    for _ in range(20):
        intent = _norm_relative(rng, house)
        room = intent.calls[0]["arguments"]["room"]
        norm = house.norm(room.capitalize())
        follow = intent.then(house)
        if norm is not None:
            step = 1 if intent.direction == "up" else -1
            assert follow == [call("set_room_norm", room=room, temperature=max(16, min(28, norm + step)))]
            return
    raise AssertionError("no room with a norm drawn")


def test_a_phrasing_saying_the_opposite_is_dropped():
    off = Intent("control", "", ["кухн"], [], action="off")
    assert phrase_ok(off, "Выключи свет на кухне")
    assert phrase_ok(off, "Джарвис, погаси свет на кухне")
    assert not phrase_ok(off, "Включи свет на кухне")  # seen from the teacher
    assert not phrase_ok(off, "Включи везде свет и выключи")
    assert not phrase_ok(off, "Выключи свет в спальне")  # the room is missing
    close = Intent("valve", "", [], [], action="close")
    assert phrase_ok(close, "Перекрой воду") and not phrase_ok(close, "Открой воду")


def test_answers_have_the_right_cases_and_numbers():
    assert replies.degrees(21) == "21 градус" and replies.degrees(22) == "22 градуса"
    assert replies.degrees(25) == "25 градусов" and replies.degrees(22.5) == "22,5 градуса"
    assert replies.at("Кухня") == "на кухне" and replies.at("спальня") == "в спальне"
    rng = random.Random(0)
    intent = Intent("control", "", [], [])
    pairs = [(call("control_devices", room="кухня", device="ventilation", action="on"),
              {"done": [{"room": "Кухня", "device": "ventilation", "action": "on"}]})]
    answer = replies.reply(rng, intent, pairs)
    assert "на кухне" in answer and ("включила" in answer.lower() or "включена" in answer)
    missing = [(call("control_devices", room="гостевая", device="ac", action="off"),
                {"error": "There is no ac in Гостевая.", "rooms_with_it": ["Зал"]})]
    assert replies.reply(rng, intent, missing) == "В гостевой нет кондиционера."


def test_where_it_is_hottest_is_worked_out_from_the_data():
    status = {"rooms": {"Кухня": {"temperature": "25.5 °C"}, "Спальня": {"temperature": "21.0 °C"}}}
    hottest = Intent("status", "", [], [], question="hottest")
    coldest = Intent("status", "", [], [], question="coldest")
    pair = [(call("get_home_status"), status)]
    assert replies.reply(random.Random(0), hottest, pair) == "Теплее всего на кухне, 25,5 градуса."
    assert replies.reply(random.Random(0), coldest, pair) == "Прохладнее всего в спальне, 21 градус."


def test_a_conversation_is_calls_results_and_an_answer():
    house_seed = 11
    house = SimHouse(random.Random(house_seed))
    room = house.rooms[0].casefold()
    intent = Intent("norm", "", [], [call("set_room_norm", room=room, temperature=21)])
    conv = asyncio.run(build_conversation(random.Random(0), house_seed, intent, f"держи {room} 21"))
    roles = [m["role"] for m in conv.messages]
    assert roles[:2] == ["system", "user"] and roles[2] == "assistant" and "tool" in roles
    assert conv.messages[-1]["role"] == "assistant" and conv.messages[-1]["content"]
    assert "[Current local time:" in conv.messages[1]["content"]
    effects = asyncio.run(expected_effects(house_seed, intent, "держи 21"))
    assert effects and effects[0][:2] == ["input_number", "set_value"]


class CharTokenizer:
    """Every character a token - enough to check what the loss sees."""

    pad_token_id = 0

    def apply_chat_template(self, messages, tools=None, tokenize=False):
        return "".join(f"<|im_start|>{m['role']}\n{m.get('content') or json.dumps(m.get('tool_calls'))}<|im_end|>\n"
                       for m in messages)

    def __call__(self, text, return_offsets_mapping=False, add_special_tokens=False):
        return {"input_ids": [ord(c) for c in text], "offset_mapping": [(i, i + 1) for i in range(len(text))]}


def test_only_jarvis_turns_are_learned():
    record = {"messages": [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USER"},
                           {"role": "assistant", "content": "JARVIS"}], "tools": []}
    example = encode(CharTokenizer(), record, max_len=10_000)
    learned = "".join(chr(t) for t, label in zip(example["input_ids"], example["labels"]) if label != -100)
    assert learned == "JARVIS<|im_end|>"
    assert encode(CharTokenizer(), record, max_len=10) is None  # too long: dropped, not cut



def test_questions_and_complaints_carry_no_commands_or_copied_words():
    cooler = Intent("norm_relative", "", ["балк"], [], required_any=("прохлад", "холод", "охлад"), no_commands=True)
    assert phrase_ok(cooler, "Сделай на балконе прохладнее")
    assert not phrase_ok(cooler, "Джарвис, вруби на балконе")  # seen from a teacher
    cold = Intent("complaint", "", ["гост"], [], required_any=("холод",), no_commands=True)
    assert phrase_ok(cold, "Что-то в гостиной холодно")
    assert not phrase_ok(cold, "Холодно в гостиной, жалуюсь")  # copied from the task
    question = Intent("status", "", ["кухн"], [], no_commands=True)
    assert phrase_ok(question, "Что включено на кухне?")
    assert not phrase_ok(question, "Выключи всё на кухне")


def test_the_numbers_said_are_exactly_the_intents():
    hold = Intent("norm", "", ["гост", "18"], [])
    assert phrase_ok(hold, "В гостиной держи 18 градусов")
    assert not phrase_ok(hold, "В гостиной 18,5 градусов, держи")  # seen from the teacher
    assert phrase_ok(Intent("norm", "", ["балк", "22.5"], []), "На балконе держи 22,5")
    assert not phrase_ok(Intent("complaint", "", ["зал"], []), "В зале 30 градусов жара")
    assert phrase_ok(Intent("norm", "", ["прих", "900"], []), "Держи CO2 в прихожей до 900")


def test_a_bare_main_valve_is_water_not_gas():
    rng, house = random.Random(0), SimHouse(random.Random(0))
    from training.intents import _valve

    for _ in range(40):
        intent = _valve(rng, house)
        if intent.calls[0]["arguments"]["device"] == "gas_valve" and intent.action == "open":
            assert not phrase_ok(intent, "Открой главный кран")  # the teacher wrote this for gas
            assert phrase_ok(intent, "Открой газ")
            return
    raise AssertionError("no gas opening drawn")


def test_the_exam_clock_is_the_cases_own_not_the_machines():
    from app.llm.base import ContentBlock, LLMResponse
    from training.evaluate import run_case

    seen = []

    class Echo:
        async def generate(self, *, system, messages, tools):
            seen.append(messages[-1]["content"])
            return LLMResponse(content=[ContentBlock(type="text", text="ok")], stop_reason="end_turn")

    case = {"house_seed": 5, "said": "спокойной ночи", "kind": "chat", "effects": []}
    first = asyncio.run(run_case(Echo(), case))["time"]
    assert asyncio.run(run_case(Echo(), case))["time"] == first and f" {first}, " in seen[0]
    assert asyncio.run(run_case(Echo(), case, hour=8))["time"] == "08:00" and " 08:00, " in seen[-1]


def test_the_wake_word_is_put_back_like_speech_has_it():
    from training.build_dataset import _with_name

    rng = random.Random(0)
    said = {_with_name(rng, "Включи свет на кухне!") for _ in range(60)}
    assert said == {"Включи свет на кухне!", "Джарвис, включи свет на кухне!", "Включи свет на кухне, Джарвис!"}
    assert _with_name(rng, "Джарвис, я дома") == "Джарвис, я дома"
    house = SimHouse(random.Random(3))
    intent = Intent("scenario", "", [], [call("run_scenario", name="?")], name_from_phrase=True)
    for seed in range(6):
        conv = asyncio.run(build_conversation(random.Random(seed), 3, intent, "Я дома"))
        assert conv.messages[2]["tool_calls"][0]["function"]["arguments"]["name"] == "я дома"  # never the name
