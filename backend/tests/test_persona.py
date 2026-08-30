import pytest

from app.db import connect
from app.memory import MemoryStore
from app.persona import build_persona_prompt, get_style, update_style


@pytest.fixture
def memory(tmp_path):
    return MemoryStore(connect(str(tmp_path / "test.db")))


def test_butler_prompt_is_static(memory):
    prompt = build_persona_prompt("butler", memory, "ivan")
    assert "butler" in prompt.lower()


def test_warm_prompt_is_static(memory):
    prompt = build_persona_prompt("warm", memory, "ivan")
    assert "warm" in prompt.lower()


def test_unknown_persona_mode_raises(memory):
    with pytest.raises(ValueError):
        build_persona_prompt("grumpy", memory, "ivan")


def test_adaptive_prompt_starts_neutral(memory):
    prompt = build_persona_prompt("adaptive", memory, "ivan")
    assert "adaptive" in prompt.lower()
    assert "read the room on formality" in prompt


def test_default_style_is_neutral(memory):
    style = get_style(memory, "ivan")
    assert style.formality == 0.5
    assert style.verbosity == 0.5
    assert style.humor == 0.5


def test_formal_message_shifts_formality_up(memory):
    style = update_style(memory, "ivan", "Не могли бы Вы, пожалуйста, подсказать?")
    assert style.formality > 0.5


def test_informal_message_shifts_formality_down(memory):
    style = update_style(memory, "ivan", "ты не подскажешь?")
    assert style.formality < 0.5


def test_short_message_shifts_verbosity_down(memory):
    style = update_style(memory, "ivan", "привет")
    assert style.verbosity < 0.5


def test_long_message_shifts_verbosity_up(memory):
    long_message = " ".join(["слово"] * 30)
    style = update_style(memory, "ivan", long_message)
    assert style.verbosity > 0.5


def test_style_persists_across_calls(memory):
    update_style(memory, "ivan", "Не могли бы Вы, пожалуйста, подсказать?")
    persisted = get_style(memory, "ivan")
    assert persisted.formality > 0.5


def test_repeated_signal_moves_further_than_one_message(memory):
    update_style(memory, "ivan", "ты как?")
    once = get_style(memory, "ivan").formality
    update_style(memory, "ivan", "ты как?")
    twice = get_style(memory, "ivan").formality
    assert twice < once  # keeps drifting informal, doesn't jump straight there
