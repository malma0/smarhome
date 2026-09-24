"""classify() is tested on the kind of text Vosk actually produced for the
test phrases (lowercase, no punctuation). WakeWordDetector itself needs the
46 MB model and real audio - checked live, see the module docstring."""

import json

from app.wake_word import (
    DEFAULT_WAKE_WORDS,
    NONE,
    WAKE_ONLY,
    WAKE_WITH_COMMAND,
    StreamingTranscript,
    classify,
    parse_wake_words,
)

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


class _FakeRecognizer:
    """Mimics Vosk: segments finalized mid-stream (AcceptWaveform -> True)
    are only in Result(), never repeated in FinalResult()."""

    def __init__(self, script):
        self._script = list(script)  # per feed: (finalized?, text)
        self._last = ""

    def AcceptWaveform(self, data):
        done, text = self._script.pop(0)
        self._last = text
        return done

    def Result(self):
        return json.dumps({"text": self._last})

    def FinalResult(self):
        return json.dumps({"text": "включи свет"})


def test_streaming_collects_segments_finalized_along_the_way():
    stream = StreamingTranscript(_FakeRecognizer([(False, ""), (True, "эй джарвис"), (False, "")]))
    for _ in range(3):
        stream.feed(b"\x00\x00")
    text = stream.finish()
    assert text == "эй джарвис включи свет"
    assert classify(text, W) == WAKE_WITH_COMMAND


def test_parse_wake_words_from_env_value():
    assert parse_wake_words(" Пятница , ПЯТНИЦУ ") == ("пятница", "пятницу")
    assert parse_wake_words("") == DEFAULT_WAKE_WORDS
