"""The shopping list - "добавь в список молоко и хлеб", "что купить?",
"молоко купил". Kept in Jarvis's own SQLite database; one list for the house.
"""

import sqlite3

from app.reminders import local_now
from app.tools.registry import Tool, ToolRegistry, TurnContext


def _norm(text: str) -> str:
    return " ".join(text.casefold().replace("ё", "е").split())


def _stem(word: str) -> str:
    """'молока' and 'молоко' alike: the word without its last two letters."""
    return word[: max(3, len(word) - 2)]


def matches(query: str, item: str) -> bool:
    """'молоко' finds 'молоко 2 литра', 'хлеба' finds 'хлеб'."""
    words = _norm(query).split()
    return bool(words) and all(_stem(w) in _norm(item) for w in words)


class ShoppingList:
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS shopping (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item TEXT NOT NULL,
        added_at TEXT NOT NULL,
        who TEXT,  -- the resident who added it, if known
        via TEXT   -- "voice" or "app" - the app shows "Эля · голосом"
    )
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        conn.execute(self.SCHEMA)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(shopping)")}
        for column in ("who", "via"):
            if column not in columns:  # a list from before
                conn.execute(f"ALTER TABLE shopping ADD COLUMN {column} TEXT")
        conn.commit()

    def items(self) -> list[str]:
        return [row["item"] for row in self._conn.execute("SELECT item FROM shopping ORDER BY id")]

    def entries(self) -> list[dict]:
        """For the app: each item with who added it and how."""
        return [{"item": r["item"], "who": r["who"], "via": r["via"]}
                for r in self._conn.execute("SELECT item, who, via FROM shopping ORDER BY id")]

    def add(self, item: str, who: str | None = None, via: str | None = None) -> bool:
        """False if it's already there."""
        if any(_norm(item) == _norm(existing) for existing in self.items()):
            return False
        self._conn.execute("INSERT INTO shopping (item, added_at, who, via) VALUES (?, ?, ?, ?)",
                           (item, local_now().isoformat(), who, via))
        self._conn.commit()
        return True

    def remove_exact(self, item: str) -> list[str]:
        """The app's tick: that one line, not everything like it ('хлеб' would take 'хлеб бородинский' too)."""
        gone = [i for i in self.items() if _norm(i) == _norm(item)]
        for i in gone:
            self._conn.execute("DELETE FROM shopping WHERE item = ?", (i,))
        self._conn.commit()
        return gone

    def remove(self, query: str) -> list[str]:
        gone = [item for item in self.items() if matches(query, item)]
        for item in gone:
            self._conn.execute("DELETE FROM shopping WHERE item = ?", (item,))
        self._conn.commit()
        return gone

    def clear(self) -> list[str]:
        gone = self.items()
        self._conn.execute("DELETE FROM shopping")
        self._conn.commit()
        return gone


def make_handler(shopping: ShoppingList):
    async def shopping_list(tool_input: dict, ctx: TurnContext) -> dict:
        action = tool_input.get("action") or "list"
        items = [str(i).strip() for i in (tool_input.get("items") or []) if str(i).strip()]
        if action == "list":
            return {"items": shopping.items()}
        if action == "clear":
            return {"removed": shopping.clear(), "items": []}
        if not items:
            return {"error": "Name the items."}
        if action == "add":
            who = ctx.resident if ctx.resident not in ("default", "panel", "") else None
            via = "voice" if ctx.voice is not None else tool_input.get("via")  # said aloud, or typed / the app
            added = [i for i in items if shopping.add(i, who, via)]
            already = [i for i in items if i not in added]
            return {"added": added, "already_there": already, "items": shopping.items()}
        if action == "bought":  # the app's tick on one exact line
            removed = [gone for i in items for gone in shopping.remove_exact(i)]
            return {"removed": removed, "items": shopping.items()}
        if action == "remove":
            removed = [gone for i in items for gone in shopping.remove(i)]
            missing = [i for i in items if not any(matches(i, g) for g in removed)]
            return {"removed": removed, "not_on_list": missing, "items": shopping.items()}
        return {"error": "action must be add, remove, list or clear."}

    return shopping_list


def register(registry: ToolRegistry, shopping: ShoppingList) -> None:
    registry.register(
        Tool(
            name="shopping_list",
            description=(
                "The house's shopping list. add items ('добавь молоко и хлеб'), remove items (bought or not "
                "needed: 'молоко купил', 'убери хлеб'), list ('что купить?'), clear (only when asked to "
                "clear it all). Items in Russian, as said, one per entry, in the nominative ('молоко', "
                "'хлеб')."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["add", "remove", "list", "clear"]},
                    "items": {"type": ["array", "null"], "items": {"type": "string"}},
                },
                "required": ["action"],
            },
            handler=make_handler(shopping),
        )
    )
