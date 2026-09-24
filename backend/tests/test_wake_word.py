"""classify() is tested on the kind of text Vosk actually produced for the
test phrases (lowercase, no punctuation). WakeWordDetector itself needs the
46 MB model and real audio - checked live, see the module docstring."""

from app.wake_word import DEFAULT_WAKE_WORDS, NONE, WAKE_ONLY, WAKE_WITH_COMMAND, classify, parse_wake_words

W = DEFAULT_WAKE_WORDS


def test_name_with_a_command_after_it():
    assert classify("джарвис включи свет на кухне", W) == WAKE_WITH_COMMAND


def test_name_in_the_middle_still_counts():
    assert classify("слушай джарвис какая завтра погода", W) == WAKE_WITH_COMMAND


def test_the_male_voice_variant_counts_too():
    assert classify("джервис включи свет на кухне", W) == WAKE_WITH_COMMAND


def test_name_alone_or_with_fillers_is_wake_only():
    assert classify("джарвис", W) == WAKE_ONLY
    assert classify("эй джарвис", W) == WAKE_ONLY
    assert classify("ну слушай джервис", W) == WAKE_ONLY


def test_sound_alike_words_that_fooled_the_grammar_mode_do_not_wake():
    for text in (
        "джордж вчера пошёл в магазин за хлебом",
        "сегодня очень жарко давай откроем окно",
        "чарли иди сюда мне нужна помощь",
        "джинсы висят в шкафу рядом с курткой",
    ):
        assert classify(text, W) == NONE


def test_darwin_is_not_a_wake_word():
    assert classify("теория дарвина и сам дарвин", W) == NONE


def test_empty_text_is_none():
    assert classify("", W) == NONE


def test_parse_wake_words_from_env_value():
    assert parse_wake_words(" Пятница , ПЯТНИЦУ ") == ("пятница", "пятницу")
    assert parse_wake_words("") == DEFAULT_WAKE_WORDS
