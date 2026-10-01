"""The exam: how well a model runs the house - any model, same questions.

    python -m training.evaluate --model groq:openai/gpt-oss-20b
    python -m training.evaluate --model ollama:jarvis-home --base-url http://192.168.1.50:11434
    python -m training.evaluate --compare

Each case (training/data/eval.jsonl, made by build_dataset --eval) is its
own random house and a phrase. The model runs through Jarvis's real agent
and real home tools, and what's graded is what happened to the house: the
exact set of changes (switches, thermostats, norms, scenarios) has to equal
the right answer's. A question (status) has to be looked up, a chat has to
stay a chat. So "кухня" vs "на кухне" in the arguments doesn't matter - only
whether the right lamp went off.

Results are saved per model (training/results/), and a run picks up where it
stopped - free-tier daily limits end runs early.
"""

import argparse
import asyncio
import json
import random
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app.agent import JarvisAgent, current_time_note
from app.db import connect
from app.domains import home
from app.memory import MemoryStore
from app.tools.registry import Tool, ToolRegistry
from app.domains.home import match_room
from training.build_dataset import _random_time
from training.sim_house import SimHouse

EVAL_FILE = Path(__file__).parent / "data" / "eval.jsonl"
# The exam's tools are the ones the home model was trained on: the live ones
# grew (curtains, history...), and a changed tool text would make old and
# new scores incomparable.
TRAINED_TOOLS_FILE = Path(__file__).parents[1] / "app" / "llm" / "home_model_tools.json"
HISTORY_SLACK = timedelta(minutes=60)


def load_tools(path: Path = TRAINED_TOOLS_FILE) -> list[dict]:
    return json.loads(Path(path).read_text("utf-8"))


def same_history_call(actual: dict, expected: dict, now: datetime) -> bool:
    """The right sensor and room, and the period within an hour of the right one."""
    if actual.get("what") != expected.get("what"):
        return False
    if expected.get("room") and match_room(actual.get("room") or "", [expected["room"].capitalize()]) is None:
        return False
    for key in ("start", "end"):
        try:
            got = datetime.fromisoformat(str(actual.get(key) or now.strftime("%Y-%m-%dT%H:%M")))
            want = datetime.fromisoformat(expected[key])
        except ValueError:
            return False
        if abs(got.replace(tzinfo=None) - want.replace(tzinfo=None)) > HISTORY_SLACK:
            return False
    return True
RESULTS = Path(__file__).parent / "results"


def make_llm(spec: str, base_url: str | None):
    kind, _, model = spec.partition(":")
    if kind == "groq":
        from app.config import settings
        from app.llm.groq import GroqProvider

        return GroqProvider(api_key=settings.groq_api_key, model=model, base_url=settings.groq_base_url,
                            temperature=settings.groq_temperature)  # no fallbacks: one model's own score
    if kind in ("ollama", "qwen"):  # qwen: Ollama with the prompt built by the training template
        from app.llm.ollama import OllamaProvider

        return OllamaProvider(model=model, base_url=base_url or "http://localhost:11434", qwen_raw=kind == "qwen")
    raise SystemExit(f"Unknown model spec {spec!r} - use groq:<model>, ollama:<model> or qwen:<model>")


def _effects(calls) -> list[str]:
    return sorted(json.dumps(list(c), ensure_ascii=False, sort_keys=True) for c in calls)


async def run_case(llm, case: dict, hour: int | None = None, tools_spec: list[dict] | None = None) -> dict:
    # The clock the model sees is the case's own, not the machine's - an exam run
    # in the morning shouldn't differ from one at night. --hour pins it for all.
    when = _random_time(random.Random(case["house_seed"]))
    if hour is not None:
        when = when.replace(hour=hour, minute=0)
    house = SimHouse(random.Random(case["house_seed"]))
    handlers = dict(zip(("get_home_status", "control_devices", "set_room_norm", "run_scenario"),
                        home.make_handlers(house)))
    handlers.update(zip(("home_history", "house_schedule"), home.make_more_handlers(house, now=lambda: when)))
    tools = ToolRegistry()
    for definition in tools_spec or load_tools():
        f = definition["function"]
        tools.register(Tool(name=f["name"], description=f["description"], parameters=f["parameters"],
                            handler=handlers[f["name"]]))
    agent = JarvisAgent(llm=llm, tools=tools, memory=MemoryStore(connect(":memory:")))
    started = time.monotonic()
    with patch("app.agent.current_time_note", lambda now=None: current_time_note(when)):
        result = await agent.chat("eval", "default", case["said"], spoken=True)
    used = [a["tool"] for a in result["actions"]]
    got, want = _effects(house.calls), _effects(case["effects"])
    if case["kind"] in ("status", "house_status"):
        ok = got == [] and "get_home_status" in used
    elif case["kind"] == "history":
        expected = case["expected_calls"][0]["arguments"]
        ok = got == [] and any(a["tool"] == "home_history" and same_history_call(a["input"], expected, when)
                               for a in result["actions"])
    elif case["kind"] == "schedule" and not want:  # "какие расписания" - looked up, nothing changed
        ok = got == [] and "house_schedule" in used
    elif case["kind"] == "chat":
        ok = used == []
    elif case["kind"] == "brightness" and not want:  # nothing to change - but it has to have looked
        ok = got == [] and "get_home_status" in used
    else:
        ok = got == want
    return {"said": case["said"], "kind": case["kind"], "ok": ok, "seconds": round(time.monotonic() - started, 2),
            "tools": [(a["tool"], a["input"]) for a in result["actions"]], "reply": result["response"],
            "got": got, "want": want, "time": f"{when:%H:%M}"}


def evaluate(spec: str, base_url: str | None, limit: int | None, tag: str = "", hour: int | None = None,
             tools_file: Path = TRAINED_TOOLS_FILE,
             cases_file: Path = EVAL_FILE) -> Path:
    cases = [json.loads(line) for line in cases_file.open(encoding="utf-8")][:limit]
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / (spec.replace(":", "_").replace("/", "_") + (f"_{tag}" if tag else "") + ".jsonl")
    done = {json.loads(line)["said"] for line in out.open(encoding="utf-8")} if out.exists() else set()
    async def run_all() -> None:
        llm = make_llm(spec, base_url)  # one event loop: the model's HTTP client belongs to it
        with out.open("a", encoding="utf-8") as f:
            for n, case in enumerate(cases, 1):
                if case["said"] in done:
                    continue
                try:
                    row = await run_case(llm, case, hour, load_tools(tools_file))
                except Exception as exc:  # noqa: BLE001 - a daily limit: stop, the next run continues
                    print(f"stopped at case {n}: {exc!r}"[:200])
                    break
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                f.flush()
                print(f"{n}/{len(cases)} {'OK ' if row['ok'] else 'BAD'} [{row['kind']}] {row['said'][:60]}",
                      flush=True)

    asyncio.run(run_all())
    return out


def summary() -> None:
    kinds = ["control", "multi_room", "whole_house", "multi_device", "status", "norm", "norm_relative", "complaint",
             "scenario", "valve", "chat", "brightness", "curtains", "humidifier", "security", "house_status", "history",
             "schedule"]
    print(f"{'модель':34} {'всего':>12} " + " ".join(f"{k[:11]:>11}" for k in kinds) + "  сек")
    for path in sorted(RESULTS.glob("*.jsonl")):
        rows = [json.loads(line) for line in path.open(encoding="utf-8")]
        if not rows:
            continue
        cells = []
        for k in kinds:
            of_kind = [r for r in rows if r["kind"] == k]
            cells.append(f"{sum(r['ok'] for r in of_kind)}/{len(of_kind)}" if of_kind else "-")
        total = sum(r["ok"] for r in rows)
        seconds = sum(r["seconds"] for r in rows) / len(rows)
        print(f"{path.stem:34} {total:>5}/{len(rows):<3} {100 * total // len(rows):>3}% "
              + " ".join(f"{c:>11}" for c in cells) + f"  {seconds:.1f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--compare", action="store_true")
    parser.add_argument("--tag", default="", help="a separate results file, e.g. after retraining")
    parser.add_argument("--hour", type=int, help="the model's clock at this hour in every case")
    parser.add_argument("--cases", type=Path, default=EVAL_FILE, help="another exam, e.g. data/eval_brightness.jsonl")
    parser.add_argument("--tools", type=Path, default=TRAINED_TOOLS_FILE,
                        help="the tools the model was trained with (a newer model: training/data/tools.json)")
    args = parser.parse_args()
    if args.model:
        evaluate(args.model, args.base_url, args.limit, args.tag, args.hour, args.tools, args.cases)
    summary()


if __name__ == "__main__":
    sys.exit(main())
