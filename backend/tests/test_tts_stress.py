"""The real ruaccent model (~195 MB, ~16s to load) isn't used here - the
conversion logic is tested directly, and RussianStresser with a fake
accentizer that returns ruaccent's actual output format."""

from app.tts.stress import ACUTE, RussianStresser, drop_uninformative_marks, plus_marks_to_acute


def test_plus_before_vowel_becomes_a_combining_acute_after_it():
    assert plus_marks_to_acute("сл+ово") == f"сло{ACUTE}во"
    assert plus_marks_to_acute("З+амок") == f"За{ACUTE}мок"


def test_plus_not_before_a_vowel_is_left_alone():
    assert plus_marks_to_acute("C++ и 5+ баллов") == "C++ и 5+ баллов"


def test_single_vowel_words_lose_their_mark_multi_vowel_words_keep_it():
    assert drop_uninformative_marks(f"мы{ACUTE} ви{ACUTE}дим све{ACUTE}т") == f"мы ви{ACUTE}дим свет"


def test_multi_vowel_words_keep_their_mark():
    text = f"на двери{ACUTE} виси{ACUTE}т замо{ACUTE}к"
    assert drop_uninformative_marks(text) == text


def test_words_with_yo_lose_the_mark_since_yo_is_always_stressed():
    assert drop_uninformative_marks(f"всё гото{ACUTE}во, ёлка{ACUTE}") == f"всё гото{ACUTE}во, ёлка"


class _FakeAccentizer:
    """Returns what real ruaccent returned for these inputs (captured live)."""

    def __init__(self):
        self.seen = []

    def process_all(self, text):
        self.seen.append(text)
        return {
            "На двери висит замок.": "На двер+и вис+ит зам+ок.",
            "Сколько будет 2 плюс 2?": "Ск+олько б+удет 2 пл+юс 2?",
        }.get(text, text)


def _stresser_with(fake):
    stresser = RussianStresser()
    stresser._accentizer = fake  # skip the real ~16s model load
    return stresser


def test_stress_end_to_end_in_chatterbox_format():
    stresser = _stresser_with(_FakeAccentizer())
    assert stresser.stress("На двери висит замок.") == f"На двери{ACUTE} виси{ACUTE}т замо{ACUTE}к."


def test_literal_plus_becomes_a_spoken_word_instead_of_being_deleted():
    """Real ruaccent turned "2+2" into "22" - the plus is replaced before
    it ever reaches ruaccent."""
    fake = _FakeAccentizer()
    result = _stresser_with(fake).stress("Сколько будет 2+2?")
    assert fake.seen == ["Сколько будет 2 плюс 2?"]
    assert result == f"Ско{ACUTE}лько бу{ACUTE}дет 2 плюс 2?"
