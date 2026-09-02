import pytest

from app.db import connect
from app.memory import MemoryStore
from app.persona import (
    build_persona_prompt,
    gender_prompt_note,
    get_resident_gender,
    get_style,
    resolve_gendered_notation,
    update_resident_gender,
    update_style,
)


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


# --- resident gender ---


def test_gender_unknown_by_default(memory):
    assert get_resident_gender(memory, "ivan") is None


def test_feminine_past_tense_verb_detected(memory):
    gender = update_resident_gender(memory, "ivan", "я вчера сделала уроки")
    assert gender == "female"
    assert get_resident_gender(memory, "ivan") == "female"


def test_masculine_past_tense_verb_detected(memory):
    gender = update_resident_gender(memory, "ivan", "я вчера сделал уроки")
    assert gender == "male"
    assert get_resident_gender(memory, "ivan") == "male"


def test_feminine_short_adjective_detected(memory):
    assert update_resident_gender(memory, "ivan", "я готова выходить") == "female"


def test_masculine_short_adjective_detected(memory):
    assert update_resident_gender(memory, "ivan", "я готов выходить") == "male"


def test_ambiguous_message_without_self_reference_stays_unknown(memory):
    assert update_resident_gender(memory, "ivan", "какая погода завтра?") is None
    assert get_resident_gender(memory, "ivan") is None


def test_gender_is_sticky_once_set(memory):
    update_resident_gender(memory, "ivan", "я сделала уроки")
    # A later, contradictory message must not flip it back and forth.
    result = update_resident_gender(memory, "ivan", "я сделал ошибку")
    assert result == "female"
    assert get_resident_gender(memory, "ivan") == "female"


def test_gender_prompt_note_mentions_feminine_forms_when_known():
    note = gender_prompt_note("female")
    assert "feminine" in note.lower()


def test_gender_prompt_note_mentions_masculine_forms_when_known():
    note = gender_prompt_note("male")
    assert "masculine" in note.lower()


def test_gender_prompt_note_warns_against_parenthetical_notation_when_unknown():
    note = gender_prompt_note(None)
    assert "сделал(а)" in note


def test_resolve_gendered_notation_strips_to_base_form_when_unknown():
    assert resolve_gendered_notation("Спасибо, что спросил(а)!", None) == "Спасибо, что спросил!"


def test_resolve_gendered_notation_strips_to_base_form_when_male():
    assert resolve_gendered_notation("Спасибо, что спросил(а)!", "male") == "Спасибо, что спросил!"


def test_resolve_gendered_notation_resolves_to_feminine_form_when_female():
    assert resolve_gendered_notation("Спасибо, что спросил(а)!", "female") == "Спасибо, что спросила!"


def test_resolve_gendered_notation_leaves_plain_text_untouched():
    text = "Включаю свет в спальне."
    assert resolve_gendered_notation(text, "female") == text
