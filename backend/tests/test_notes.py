"""Jarvis's memory of the residents: "запомни, что я пью кофе без сахара", about someone else or the
household, what it knows, forgetting - and that the speaker's notes reach the main model's prompt."""

import asyncio

import pytest

from app import notes
from app.agent import JarvisAgent
from app.db import connect
from app.memory import MemoryStore
from app.tools import router
from app.tools.registry import ToolRegistry, TurnContext
from tests.test_agent import FakeLLM, text_response


@pytest.fixture
def memory(tmp_path):
    store = MemoryStore(connect(str(tmp_path / "t.db")))
    for name in ("default", "Матвей", "Эля"):
        store.ensure_resident(name)
    return store


def _run(memory, resident="Матвей", **tool_input):
    return asyncio.run(notes.make_handler(memory)(tool_input, TurnContext(resident=resident)))


def test_remembered_for_the_speaker_someone_else_or_the_household(memory):
    assert _run(memory, text="пьёт кофе без сахара")["about"] == "Матвей"
    assert _run(memory, text="любит потеплее в спальне", about="Эле")["about"] == "Эля"
    assert _run(memory, text="кот Барсик", about="дом")["about"] == "дом"
    assert _run(memory, resident="default", text="ужин в 19")["about"] == "дом"  # a voice it didn't know: the house's
    assert "already" in _run(memory, text="Пьёт кофе без сахара")
    assert "error" in _run(memory, text="x", about="Пете")
    assert _run(memory, action="list")["notes"] == {"Матвей": ["пьёт кофе без сахара"], "дом": ["кот Барсик", "ужин в 19"]}
    assert notes.notes(memory, "Эля") == ["любит потеплее в спальне"]


def test_forgetting_by_words_or_everything(memory):
    for text in ("пьёт кофе без сахара", "встаёт в 7", "болеет за Зенит"):
        _run(memory, text=text)
    assert _run(memory, action="forget", text="кофе")["forgot"] == ["пьёт кофе без сахара"]
    assert "error" in _run(memory, action="forget", text="чай")
    _run(memory, action="forget", all=True)
    assert notes.notes(memory, "Матвей") == []


def test_only_the_last_notes_are_kept(memory):
    for i in range(notes.MAX_NOTES + 5):
        _run(memory, text=f"заметка {i}")
    kept = notes.notes(memory, "Матвей")
    assert len(kept) == notes.MAX_NOTES and kept[0] == "заметка 5"


def test_the_speakers_notes_and_the_households_go_to_the_main_model(memory):
    _run(memory, text="пьёт кофе без сахара")
    _run(memory, resident="Эля", text="любит потеплее")
    _run(memory, text="кот Барсик", about="дом")
    llm = FakeLLM([text_response("Хорошо."), text_response("Ок.")])
    agent = JarvisAgent(llm=llm, tools=ToolRegistry(), memory=memory)
    asyncio.run(agent.chat("s", "Матвей", "привет"))
    prompt = llm.generate.await_args.kwargs["system"]
    assert "пьёт кофе без сахара" in prompt and "кот Барсик" in prompt and "любит потеплее" not in prompt
    asyncio.run(agent.chat("s2", "default", "привет"))  # nobody in particular: only the household's
    prompt = llm.generate.await_args.kwargs["system"]
    assert "кот Барсик" in prompt and "кофе" not in prompt


def test_the_words_that_reach_the_memory():
    assert "memory" in router.select("Джарвис, запомни, что я пью кофе без сахара", None)
    assert "memory" in router.select("что ты обо мне знаешь?", None)
    assert "memory" in router.select("забудь про кофе", None)
    assert "memory" not in router.select("включи свет на кухне", None)
