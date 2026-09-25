"""Timers and reminders - "поставь таймер на 10 минут", "напомни в 8 выключить
духовку".

Kept in Jarvis's own SQLite database, so they survive a restart; a timer
due while Jarvis was off rings as soon as it's back. The voice loop
(voice_app.run_hands_free) checks for due ones between phrases and rings
them: a chime, the text in the offline voice, a banner with "Стоп". The
model sets them through one tool, `reminders`, with a time it works out
from the current local time given in its prompt (app.agent).
"""

import sqlite3
from datetime import datetime, timedelta

from app.tools.registry import Tool, ToolRegistry, TurnContext

MAX_AHEAD = timedelta(days=366)
KINDS = ("timer", "reminder")


def local_now() -> datetime:
    return datetime.now().astimezone()


def _plural(n: int, one: str, few: str, many: str) -> str:
    m10, m100 = n % 10, n % 100
    if m10 == 1 and m100 != 11:
        return one
    if 2 <= m10 <= 4 and not 12 <= m100 <= 14:
        return few
    return many


def human_duration(seconds: float) -> str:
    """125 -> '2 минуты 5 секунд', 5400 -> '1 час 30 минут'."""
    seconds = max(0, round(seconds))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    for n, forms in ((days, ("день", "дня", "дней")), (hours, ("час", "часа", "часов")),
                     (minutes, ("минута", "минуты", "минут")), (secs, ("секунда", "секунды", "секунд"))):
        if n:
            parts.append(f"{n} {_plural(n, *forms)}")
    if days:  # "через 2 дня 3 часа" - seconds and minutes are noise by then
        parts = parts[:2]
    return " ".join(parts) or "0 секунд"


def timer_duration_words(seconds: float) -> str:
    """For the timer's own name: 'на 10 минут' (accusative, 'минута' -> 'минуту')."""
    return human_duration(seconds).replace("1 минута", "1 минуту").replace("1 секунда", "1 секунду")


class ReminderStore:
    """On the voice loop's thread only - that's where its SQLite connection lives."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        resident_id TEXT,
        kind TEXT NOT NULL,
        text TEXT NOT NULL,
        due_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        done INTEGER NOT NULL DEFAULT 0
    )
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        conn.execute(self.SCHEMA)
        conn.commit()

    def add(self, kind: str, text: str, due: datetime, resident_id: str | None = None) -> dict:
        cur = self._conn.execute(
            "INSERT INTO reminders (resident_id, kind, text, due_at, created_at) VALUES (?, ?, ?, ?, ?)",
            (resident_id, kind, text, due.isoformat(), local_now().isoformat()),
        )
        self._conn.commit()
        return self.get(cur.lastrowid)

    def get(self, reminder_id: int) -> dict | None:
        row = self._conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
        return self._item(row) if row else None

    def pending(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM reminders WHERE done = 0").fetchall()
        return sorted((self._item(r) for r in rows), key=lambda r: r["due"])

    def due(self, now: datetime) -> list[dict]:
        return [r for r in self.pending() if r["due"] <= now]

    def mark_done(self, reminder_id: int) -> None:
        self._conn.execute("UPDATE reminders SET done = 1 WHERE id = ?", (reminder_id,))
        self._conn.commit()

    @staticmethod
    def _item(row) -> dict:
        return {"id": row["id"], "kind": row["kind"], "text": row["text"],
                "due": datetime.fromisoformat(row["due_at"]), "resident_id": row["resident_id"]}


def _parse_at(at: str, now: datetime) -> datetime:
    """'2026-09-26T08:00' (local, as the model writes it) -> aware datetime."""
    when = datetime.fromisoformat(at.strip().replace(" ", "T"))
    return when.replace(tzinfo=now.tzinfo) if when.tzinfo is None else when


def _public(item: dict, now: datetime) -> dict:
    return {"id": item["id"], "kind": item["kind"], "text": item["text"],
            "at": item["due"].astimezone(now.tzinfo).strftime("%Y-%m-%d %H:%M"),
            "in": human_duration((item["due"] - now).total_seconds())}


def make_handler(store: ReminderStore, now=local_now):
    async def reminders(tool_input: dict, ctx: TurnContext) -> dict:
        action = tool_input.get("action") or "add"
        current = now()
        if action == "list":
            return {"pending": [_public(r, current) for r in store.pending()]}

        if action == "cancel":
            pending = store.pending()
            if tool_input.get("id") is not None:
                chosen = [r for r in pending if r["id"] == int(tool_input["id"])]
            elif tool_input.get("text"):
                words = str(tool_input["text"]).casefold()
                chosen = [r for r in pending if words in r["text"].casefold()]
            elif tool_input.get("kind") in KINDS:
                chosen = [r for r in pending if r["kind"] == tool_input["kind"]]
            else:
                chosen = pending if tool_input.get("all") else []
            if not chosen:
                return {"error": "Nothing matched to cancel.", "pending": [_public(r, current) for r in pending]}
            for r in chosen:
                store.mark_done(r["id"])
            return {"cancelled": [_public(r, current) for r in chosen]}

        if action != "add":
            return {"error": "action must be add, list or cancel."}
        kind = tool_input.get("kind") or "reminder"
        if kind not in KINDS:
            return {"error": "kind must be timer or reminder."}
        in_seconds, at = tool_input.get("in_seconds"), tool_input.get("at")
        if (in_seconds is None) == (at is None):
            return {"error": "Give exactly one of in_seconds (from now) or at (local 'YYYY-MM-DDTHH:MM')."}
        try:
            due = current + timedelta(seconds=float(in_seconds)) if in_seconds is not None else _parse_at(at, current)
        except (TypeError, ValueError):
            return {"error": f"Can't read the time {at!r} - use local 'YYYY-MM-DDTHH:MM'."}
        if due <= current:
            return {"error": "That time has already passed.", "now": current.strftime("%Y-%m-%d %H:%M")}
        if due - current > MAX_AHEAD:
            return {"error": "More than a year ahead - too far."}
        text = (tool_input.get("text") or "").strip()
        if kind == "timer":
            length = f"Таймер на {timer_duration_words((due - current).total_seconds())}"
            text = f"{length}: {text}" if text else length
        elif not text:
            return {"error": "A reminder needs its text - what to remind about."}
        item = store.add(kind, text, due)
        return {"added": _public(item, current)}

    return reminders


def register(registry: ToolRegistry, store: ReminderStore) -> None:
    registry.register(
        Tool(
            name="reminders",
            description=(
                "Timers and reminders. add (default): kind timer ('таймер на 10 минут': in_seconds, optional "
                "text) or reminder (text, plus in_seconds or at = local 'YYYY-MM-DDTHH:MM' from the current "
                "time). list. cancel: by id, words of its text, kind, or all=true. Due ones ring by "
                "themselves."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": ["string", "null"], "enum": ["add", "list", "cancel", None]},
                    "kind": {"type": ["string", "null"], "enum": ["timer", "reminder", None]},
                    "text": {"type": ["string", "null"]},
                    "in_seconds": {"type": ["number", "null"]},
                    "at": {"type": ["string", "null"]},
                    "id": {"type": ["integer", "null"]},
                    "all": {"type": ["boolean", "null"]},
                },
            },
            handler=make_handler(store),
        )
    )
