""""Что у меня сегодня?" / "а завтра?" - the day in one answer: the weather, the speaker's
reminders for that day and everyone's, the timers running, the shopping list.

One tool instead of the model collecting it from four: fewer tokens, nothing forgotten, and
only this resident's reminders (whoever set them, or "напомни Эле" - app.reminders), not the
whole house's. Asked, never volunteered (docs/TZ.md, the attention rule).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.reminders import NOBODY, ReminderStore, human_duration, local_now, repeat_words
from app.tools.registry import Tool, ToolRegistry, TurnContext


def day_of(store: ReminderStore, resident: str, day: datetime, now: datetime) -> dict:
    """The reminders due on that day (from now on, if it's today) - this resident's and no one's."""
    start = max(now, day.replace(hour=0, minute=0, second=0, microsecond=0))
    end = day.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    reminders, timers = [], []
    for r in store.pending():
        if r["resident_id"] not in NOBODY and resident not in NOBODY and r["resident_id"] != resident:
            continue  # someone else's
        due = r["due"].astimezone(now.tzinfo)
        if r["kind"] == "timer":
            if due.date() == now.date() and day.date() == now.date():
                timers.append({"text": r["text"], "left": human_duration((due - now).total_seconds())})
            continue
        if start <= due < end:
            item = {"at": f"{due:%H:%M}", "text": r["text"]}
            if r.get("repeat"):
                item["repeat"] = repeat_words(r["repeat"])
            if r["resident_id"] not in NOBODY and r["resident_id"] != resident:
                item["for"] = r["resident_id"]
            reminders.append(item)
    return {"reminders": reminders, "timers": timers}


def make_handler(store: ReminderStore, shopping=None, weather=None, now=local_now):
    async def my_day(tool_input: dict, ctx: TurnContext) -> dict:
        current = now()
        tomorrow = (tool_input.get("day") or "today") == "tomorrow"
        day = current + timedelta(days=1) if tomorrow else current
        out = {"day": "завтра" if tomorrow else "сегодня", "date": f"{day:%Y-%m-%d}", **day_of(store, ctx.resident, day, current)}
        if weather is not None:
            forecast = await weather({"days": 2 if tomorrow else 1}, ctx)
            if "error" in forecast:
                out["weather"] = forecast["error"]
            else:
                out["weather"] = forecast["days"][-1] if forecast.get("days") else {}
                if not tomorrow:
                    out["weather_now"] = forecast.get("now")
        if shopping is not None:
            out["shopping_list"] = shopping.items()
        return out

    return my_day


def register(registry: ToolRegistry, store: ReminderStore, shopping=None, weather=None) -> None:
    registry.register(
        Tool(
            name="my_day",
            description=(
                "The resident's day in one call: 'что у меня сегодня?', 'какие планы на завтра?', 'что сегодня?'. "
                "Weather, their reminders for that day (and everyone's), timers running, the shopping list. "
                "day: today (default) or tomorrow. Tell it briefly: the weather in a phrase, then what's planned; "
                "the shopping list only if it isn't empty."
            ),
            parameters={
                "type": "object",
                "properties": {"day": {"type": ["string", "null"], "enum": ["today", "tomorrow", None]}},
            },
            handler=make_handler(store, shopping, weather),
        )
    )
