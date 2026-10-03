import asyncio

from app.db import connect
from app.shopping import ShoppingList, make_handler, matches
from app.tools import router
from app.tools.registry import TurnContext


def _run(handler, **tool_input):
    return asyncio.run(handler(tool_input, TurnContext()))


def test_add_list_remove_clear(tmp_path):
    handler = make_handler(ShoppingList(connect(str(tmp_path / "j.db"))))
    added = _run(handler, action="add", items=["молоко 2 литра", "хлеб"])
    assert added["added"] == ["молоко 2 литра", "хлеб"]
    assert _run(handler, action="add", items=["Хлеб"])["already_there"] == ["Хлеб"]
    assert _run(handler, action="list")["items"] == ["молоко 2 литра", "хлеб"]
    removed = _run(handler, action="remove", items=["молоко", "сыр"])  # "молоко купил"
    assert removed["removed"] == ["молоко 2 литра"] and removed["not_on_list"] == ["сыр"]
    assert _run(handler, action="clear")["removed"] == ["хлеб"]
    assert _run(handler, action="list")["items"] == []


def test_it_survives_a_restart(tmp_path):
    ShoppingList(connect(str(tmp_path / "j.db"))).add("яйца")
    assert ShoppingList(connect(str(tmp_path / "j.db"))).items() == ["яйца"]


def test_word_forms_match():
    assert matches("молока", "молоко") and matches("хлеба", "хлеб белый")
    assert not matches("сыр", "молоко")


def test_shopping_words_reach_the_list():
    for text in ("добавь в список молоко", "что купить?", "молоко закончилось"):
        assert "shopping" in router.select(text, None), text


def test_who_added_it_and_how(tmp_path):
    shopping = ShoppingList(connect(str(tmp_path / "j.db")))
    handler = make_handler(shopping)
    asyncio.run(handler({"action": "add", "items": ["молоко"]}, TurnContext(resident="Эля", voice="resident")))
    asyncio.run(handler({"action": "add", "items": ["сыр"], "via": "app"}, TurnContext(resident="Матвей")))
    asyncio.run(handler({"action": "add", "items": ["хлеб"]}, TurnContext()))  # nobody in particular
    assert shopping.entries() == [{"item": "молоко", "who": "Эля", "via": "voice"},
                                  {"item": "сыр", "who": "Матвей", "via": "app"}, {"item": "хлеб", "who": None, "via": None}]


def test_a_list_from_before_gets_its_new_columns(tmp_path):
    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "old.db"))
    conn.execute("CREATE TABLE shopping (id INTEGER PRIMARY KEY AUTOINCREMENT, item TEXT NOT NULL, added_at TEXT NOT NULL)")
    conn.execute("INSERT INTO shopping (item, added_at) VALUES ('яйца', '2026-10-01')")
    conn.commit()
    conn.row_factory = sqlite3.Row
    assert ShoppingList(conn).entries() == [{"item": "яйца", "who": None, "via": None}]
