"""Builds the training (and evaluation) data for Jarvis's own home model.

    python -m training.build_dataset --intents 40 --out training/data/pilot.jsonl
    python -m training.build_dataset --intents 800 --out training/data/train.jsonl
    python -m training.build_dataset --intents 120 --seed 777 --phrasings 1 --eval --out training/data/eval.jsonl

For each example: a random house (training/sim_house.py), an intent with its
correct calls worked out by code (training/intents.py), a phrasing by the
teacher (training/teacher.py) - checked to name every room and number of the
intent and to say the action, not its opposite, else dropped - then the
calls run through Jarvis's real home tools on that house, and Jarvis's
spoken answer is built from the real results (training/replies.py). Written as chat conversations with tool calls (the format the
Qwen2.5 chat template renders), one per line.

--eval writes held-out cases for training/evaluate.py instead: the house
seed, what was said, and the effects the right answer has on the house.
"""

import argparse
import asyncio
import json
import random
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

from app.agent import JarvisAgent, current_time_note
from app.db import connect
from app.domains import home
from app.memory import MemoryStore
from app.tools.registry import ToolRegistry, TurnContext
from training import replies
from training.intents import Intent, draw
from training.sim_house import SimHouse
from training.teacher import Teacher

HOME_TOOLS = ("get_home_status", "control_devices", "set_room_norm", "run_scenario")
MORE_TOOLS = ("home_history", "house_schedule")
YES = ["да", "да, подтверждаю", "давай", "конечно", "да, делай", "ага, да"]


def tool_definitions() -> list[dict]:
    registry = ToolRegistry()
    home.register(registry)
    return [{"type": "function", "function": {"name": d.name, "description": d.description, "parameters": d.parameters}}
            for d in registry.definitions()]


def system_prompt(spoken: bool) -> str:
    """The same prompt Jarvis sends in production (app.agent)."""
    agent = JarvisAgent(llm=None, tools=ToolRegistry(), memory=MemoryStore(connect(":memory:")))
    return agent._build_system_prompt("default", spoken=spoken)


def _norm(text: str) -> str:
    return text.casefold().replace("ё", "е")


# The teacher sometimes wrote the opposite of what was asked ("Включи
# вентиляцию..." for turning it off) - such a phrasing is dropped, or the
# model would learn to do the opposite.
ON_WORDS = ("включ", "вруби", "зажг", "зажж", "запуст", "активир")
OFF_WORDS = ("выключ", "отключ", "выруб", "погас", "потуш", "остан", "убери")
# Orders, not states: "что включено?" is a question, "включи" is not.
COMMANDS = re.compile(r"\b(?:вы|от|в)(?:ключи|ключай|руби|рубай)\b|\b(?:погаси|потуши|зажги|запусти|останови)\b")
OPEN_WORDS = ("открой", "открыть", "откро", "пусти", "раздвин", "раздерн", "подними")
CLOSE_WORDS = ("закрой", "закрыть", "перекр", "выключ", "отключ", "перекро", "задерн", "задвин", "опусти")


# Words from the task description a teacher copied into "speech", and
# written-only forms no speech recognizer puts out.
LEAKED = ("жалую", "спрашива", "человек", "комната:", "без просьбы", "без числа", "выкл.", "°", "сделай выключ",
          "сделай включ")


NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def _numbers(text: str) -> set[str]:
    return {n.replace(",", ".") for n in NUMBER.findall(text.replace("co2", ""))}


def phrase_ok(intent: Intent, phrase: str) -> bool:
    text = _norm(phrase)
    words = [m for m in intent.must_mention if not NUMBER.fullmatch(m)]
    if not phrase or not all(m in text for m in words):
        return False
    # Exactly the intent's numbers: "18,5" is not 18, and a stray "2 градуса" is a different command.
    if _numbers(text) != _numbers(" ".join(intent.must_mention)):
        return False
    if any(w in text for w in LEAKED):
        return False
    if intent.required_any and not any(w in text for w in intent.required_any):
        return False
    if any(w in text for w in intent.forbidden_any):
        return False
    if intent.no_commands and COMMANDS.search(text):
        return False
    wanted, opposite = {"on": (ON_WORDS, OFF_WORDS), "off": (OFF_WORDS, ON_WORDS),
                        "open": (OPEN_WORDS, CLOSE_WORDS), "close": (CLOSE_WORDS, OPEN_WORDS)}.get(intent.action, ((), ()))
    if wanted and (not any(w in text for w in wanted) or any(w in text for w in opposite)):
        return False
    return True


def _spoken_name(phrase: str) -> str:
    """What was said, without the name wherever it stands ("я пришёл, Джарвис")."""
    words = [w for w in phrase.replace(",", " ").split() if w.strip(".!?").casefold() not in ("джарвис", "джервис")]
    return " ".join(words).strip(" ,.!?").lower()


def _with_name(rng: random.Random, phrase: str) -> str:
    """Live, the transcript keeps the wake word ("Джарвис, включи свет") - the
    teacher is told to leave it out, so it's put back here, as people say it."""
    if "джарвис" in phrase.casefold():
        return phrase
    where = rng.choices(["none", "start", "end"], [35, 50, 15])[0]
    if where == "start":
        return "Джарвис, " + phrase[:1].lower() + phrase[1:]
    if where == "end":
        body = phrase.rstrip(".!?")
        return f"{body}, Джарвис{phrase[len(body):]}"
    return phrase


def _random_time(rng: random.Random) -> datetime:
    start = datetime(2026, 1, 1).astimezone()
    return start + timedelta(minutes=rng.randrange(0, 365 * 24 * 60))


def _assistant_calls(calls: list[dict]) -> dict:
    return {"role": "assistant", "content": "",
            "tool_calls": [{"type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}} for c in calls]}


async def _run_calls(house: SimHouse, calls: list[dict], ctx: TurnContext, when: datetime | None = None
                     ) -> list[tuple[dict, dict]]:
    handlers = dict(zip(HOME_TOOLS, home.make_handlers(house)))
    now = when or datetime.now().astimezone()
    handlers.update(zip(MORE_TOOLS, home.make_more_handlers(house, now=lambda: now)))
    return [(c, await handlers[c["name"]](dict(c["arguments"]), ctx)) for c in calls]


def _needs_yes(result: dict) -> bool:
    return "confirmed=true" in str(result.get("error", ""))


class Conversation:
    """One example; Jarvis's answers come from training/replies.py."""

    def __init__(self, messages: list[dict], meta: dict, rng: random.Random, intent: Intent):
        self.messages, self.meta, self.rng, self.intent = messages, meta, rng, intent

    def results_message(self, pairs):
        for c, result in pairs:
            self.messages.append({"role": "tool", "name": c["name"], "content": json.dumps(result, ensure_ascii=False)})

    def reply_slot(self, said: str, pairs):
        self.messages.append({"role": "assistant", "content": replies.reply(self.rng, self.intent, pairs)})


async def build_conversation(rng, house_seed: int, intent: Intent, phrase: str) -> Conversation:
    house = SimHouse(random.Random(house_seed))
    when = _random_time(rng)
    messages = [{"role": "system", "content": system_prompt(spoken=rng.random() < 0.7)},
                {"role": "user", "content": f"{_with_name(rng, phrase)}\n\n[{current_time_note(when)}]"}]
    conv = Conversation(messages, {"kind": intent.kind, "meaning": intent.meaning, "house_seed": house_seed}, rng, intent)
    calls = [dict(c, arguments=dict(c["arguments"])) for c in (intent.calls_at(when) if intent.calls_at else intent.calls)]
    if intent.name_from_phrase:
        for c in calls:
            c["arguments"]["name"] = _spoken_name(phrase)
    if not calls:  # just talk
        conv.reply_slot(phrase, [])
        return conv

    conv.messages.append(_assistant_calls(calls))
    pairs = await _run_calls(house, calls, TurnContext(), when)
    conv.results_message(pairs)
    if intent.then and all("error" not in r for _, r in pairs):
        follow = intent.then(house)
        if follow:
            conv.messages.append(_assistant_calls(follow))
            more = await _run_calls(house, follow, TurnContext())
            conv.results_message(more)
            pairs = pairs + more
    conv.reply_slot(phrase, pairs)

    asked = [c for c, r in pairs if _needs_yes(r)]
    if asked and intent.ask_again_on_confirm and rng.random() < 0.6:
        yes = rng.choice(YES)
        conv.messages.append({"role": "user", "content": f"{yes}\n\n[{current_time_note(when + timedelta(seconds=20))}]"})
        again = [dict(c, arguments=dict(c["arguments"], confirmed=True)) for c in asked]
        conv.messages.append(_assistant_calls(again))
        done = await _run_calls(house, again, TurnContext())
        conv.results_message(done)
        conv.reply_slot(yes, done)
    return conv


def exam_time(house_seed: int) -> datetime:
    """The clock an exam case is set at - training/evaluate.py uses the same."""
    return _random_time(random.Random(house_seed))


async def expected_effects(house_seed: int, intent: Intent, phrase: str) -> list:
    """What the right answer does to the house - for evaluation."""
    house = SimHouse(random.Random(house_seed))
    when = exam_time(house_seed)
    calls = [dict(c, arguments=dict(c["arguments"])) for c in (intent.calls_at(when) if intent.calls_at else intent.calls)]
    if intent.name_from_phrase:
        for c in calls:
            c["arguments"]["name"] = _spoken_name(phrase)
    pairs = await _run_calls(house, calls, TurnContext(), when) if calls else []
    if intent.then and calls and all("error" not in r for _, r in pairs):
        follow = intent.then(house)
        if follow:
            await _run_calls(house, follow, TurnContext())
    return [list(c) for c in house.calls]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--intents", type=int, default=40)
    parser.add_argument("--phrasings", type=int, default=4)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--batch", type=int, default=12)
    parser.add_argument("--eval", action="store_true")
    parser.add_argument("--kinds", help="only these later-added kinds, e.g. brightness (training/intents.EXTRA_KINDS)")
    parser.add_argument("--out", default="training/data/pilot.jsonl")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    specs = []
    for _ in range(args.intents):
        house_seed = rng.randrange(1 << 30)
        kinds = args.kinds.split(",") if args.kinds else None
        specs.append((house_seed, draw(rng, SimHouse(random.Random(house_seed)), kinds)))

    teacher = Teacher()
    phrased: list[tuple[int, str]] = []
    dropped = 0
    for start in range(0, len(specs), args.batch):
        chunk = [(i, specs[i][1].meaning) for i in range(start, min(start + args.batch, len(specs)))]
        try:
            got = teacher.phrasings(chunk, args.phrasings + 2)  # spares for what the checks drop
        except RuntimeError as e:  # the day's limit: write what is done, a rerun completes it from the cache
            print(f"{e} Writing the first {start} intents.", flush=True)
            break
        for i, _ in chunk:
            good, seen = [], set()
            for p in got.get(i, []):
                words = frozenset(_norm(_spoken_name(p)).replace("?", " ").split())
                if phrase_ok(specs[i][1], p) and words not in seen:  # a reordering isn't a new phrasing
                    good.append(p)
                    seen.add(words)
            dropped += len(got.get(i, [])) - len(good)
            phrased += [(i, p) for p in good[:args.phrasings]]
        print(f"phrasings: {min(start + args.batch, len(specs))}/{len(specs)} intents, {len(phrased)} kept, "
              f"{dropped} dropped", flush=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if args.eval:
        with out.open("w", encoding="utf-8") as f:
            for i, phrase in phrased:
                house_seed, intent = specs[i]
                effects = asyncio.run(expected_effects(house_seed, intent, phrase))
                expected = intent.calls_at(exam_time(house_seed)) if intent.calls_at else intent.calls
                # Said the way it's said live - with the name, most of the time.
                said = _with_name(random.Random(house_seed), phrase) if args.kinds else phrase
                f.write(json.dumps({"house_seed": house_seed, "said": said, "kind": intent.kind,
                                    "meaning": intent.meaning, "first_tools": [c["name"] for c in expected],
                                    "expected_calls": expected, "effects": effects}, ensure_ascii=False) + "\n")
        print(f"wrote {len(phrased)} eval cases to {out}")
        return

    conversations = [asyncio.run(build_conversation(rng, specs[i][0], specs[i][1], p)) for i, p in phrased]
    tools = tool_definitions()
    # The tools this data was built with - a model trained on it gets exactly
    # these at run time (app/llm/home_model_tools.json) and in its exam.
    (out.parent / "tools.json").write_text(json.dumps(tools, ensure_ascii=False, indent=1), encoding="utf-8")
    with out.open("w", encoding="utf-8") as f:
        for conv in conversations:
            f.write(json.dumps({"messages": conv.messages, "tools": tools, "meta": conv.meta}, ensure_ascii=False) + "\n")
    print(f"wrote {len(conversations)} conversations to {out}")


if __name__ == "__main__":
    sys.exit(main())
