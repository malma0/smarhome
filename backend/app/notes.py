"""What a resident asked Jarvis to remember - "запомни, что я пью кофе без сахара", "запомни,
что Эля любит потеплее", "у нас кот Барсик" - and what it knows when asked ("что ты обо мне знаешь?").

Kept in the residents' preferences (Jarvis's own SQLite, never leaves the machine): a person's
notes under their id, the household's under "default". The notes of whoever is speaking and the
household's go into the system prompt (JarvisAgent), so Jarvis uses them without being asked -
coffee without sugar, a warmer bedroom for Эля. Short and few (MAX_NOTES): every request carries them.
"""

from __future__ import annotations

import json
from datetime import datetime

from app.reminders import NOBODY, match_resident
from app.tools.registry import Tool, ToolRegistry, TurnContext

KEY = "notes"
HOUSE = "default"  # the household's notes live under the id that isn't anyone
MAX_NOTES = 30  # per person: the oldest go first
MAX_LENGTH = 200
HOUSE_WORDS = {"дом", "дома", "семья", "семье", "все", "всех", "всем", "нас", "мы", "house", "everyone"}


def notes(memory, resident: str) -> list[str]:
    try:
        return [n["text"] for n in json.loads(memory.get_preference(resident, KEY) or "[]")]
    except (ValueError, TypeError, KeyError):
        return []


def _save(memory, resident: str, texts: list[str]) -> None:
    memory.ensure_resident(resident)
    now = datetime.now().astimezone().isoformat(timespec="minutes")
    memory.set_preference(resident, KEY, json.dumps([{"text": t, "at": now} for t in texts[-MAX_NOTES:]],
                                                    ensure_ascii=False))


def prompt_note(memory, resident: str) -> str:
    """The lines for the system prompt - empty when there's nothing to know."""
    own = notes(memory, resident) if resident not in NOBODY else []
    house = notes(memory, HOUSE)
    if not own and not house:
        return ""
    parts = ["What the residents asked you to remember - use it when it matters (a preference, a name, a habit), "
             "without reciting it unless asked:"]
    if own:
        parts.append(f"About {resident}, who is speaking now:\n" + "\n".join(f"- {n}" for n in own))
    if house:
        parts.append("About the household:\n" + "\n".join(f"- {n}" for n in house))
    return "\n".join(parts)


def make_handler(memory):
    def whose(about, ctx: TurnContext):
        """(id, None) - whose notes; (None, error) - a name that isn't a resident."""
        about = str(about or "").strip()
        if not about:
            return (ctx.resident if ctx.resident not in NOBODY else HOUSE), None
        if about.casefold() in HOUSE_WORDS:
            return HOUSE, None
        people = memory.list_resident_ids()
        found = match_resident(about, people)
        if found is None:
            return None, {"error": f"No resident called '{about}'.", "residents": [r for r in people if r not in NOBODY]}
        return found, None

    def label(resident: str) -> str:
        return "дом" if resident == HOUSE else resident

    async def remember(tool_input: dict, ctx: TurnContext) -> dict:
        action = tool_input.get("action") or "add"
        resident, error = whose(tool_input.get("about"), ctx)
        if error:
            return error
        current = notes(memory, resident)
        if action == "list":
            known = {label(resident): current}
            if resident != HOUSE:
                known["дом"] = notes(memory, HOUSE)
            return {"notes": known}
        if action == "forget":
            words = str(tool_input.get("text") or "").casefold().strip()
            if tool_input.get("all"):
                gone = current
            elif words:
                gone = [n for n in current if words in n.casefold()]
            else:
                return {"error": "Say what to forget (words of it) or all=true."}
            if not gone:
                return {"error": "Nothing like that was remembered.", "notes": {label(resident): current}}
            _save(memory, resident, [n for n in current if n not in gone])
            return {"forgot": gone, "about": label(resident)}
        if action != "add":
            return {"error": "action must be add, list or forget."}
        text = " ".join(str(tool_input.get("text") or "").split())[:MAX_LENGTH]
        if not text:
            return {"error": "What to remember? Give text."}
        if text.casefold() in (n.casefold() for n in current):
            return {"already": text, "about": label(resident)}
        _save(memory, resident, current + [text])
        return {"remembered": text, "about": label(resident)}

    return remember


def register(registry: ToolRegistry, memory) -> None:
    registry.register(
        Tool(
            name="remember",
            description=(
                "Jarvis's memory of the residents, when they ask: add ('запомни, что я пью кофе без сахара' - "
                "text short, third person, no name: 'пьёт кофе без сахара'), list ('что ты обо мне знаешь?'), "
                "forget (words of it, or all=true: 'забудь про кофе'). about: whose - empty for whoever speaks, "
                "a resident's name ('запомни, что Эля любит потеплее': about='Эля'), or 'дом' for the household "
                "('у нас кот Барсик')."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": ["string", "null"], "enum": ["add", "list", "forget", None]},
                    "text": {"type": ["string", "null"]},
                    "about": {"type": ["string", "null"]},
                    "all": {"type": ["boolean", "null"]},
                },
            },
            handler=make_handler(memory),
        )
    )
