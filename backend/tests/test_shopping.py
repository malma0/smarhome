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
