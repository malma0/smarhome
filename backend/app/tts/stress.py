"""Russian stress marks for the Chatterbox voice (app/tts/voicebox.py).

Why this exists: Chatterbox was trained on Russian text with stress marks
added by a "russian_text_stresser" package - its tokenizer calls that step
for every Russian input, and U+0301 (combining acute accent, the stress
mark) is in the model's vocabulary. Voicebox's bundle doesn't include that
package, so its log says "russian_text_stresser not available - Russian
stress labeling skipped" and the model guesses stresses from bare text -
the wrong stresses a resident actually complained about. Adding the marks
on our side, before the text reaches Voicebox, restores what the model
expects. (That original package is AGPL-3.0; ruaccent, used here instead,
is MIT - usable in a product that's sold.)

ruaccent writes "+" before the stressed vowel ("сл+ово"); Chatterbox's
training text used a combining acute after it ("сло́во"), so that's
converted, and marks are dropped where they carry no information - words
with a single vowel, and words with ё (always stressed).
"""

import re
import threading

ACUTE = "́"
_VOWELS = "аеёиоуыэюяАЕЁИОУЫЭЮЯ"
_PLUS_BEFORE_VOWEL_RE = re.compile(rf"\+([{_VOWELS}])")
_WORD_RE = re.compile(rf"[А-Яа-яЁё{ACUTE}]+")

DEFAULT_MODEL_SIZE = "tiny2.1"


def plus_marks_to_acute(text: str) -> str:
    return _PLUS_BEFORE_VOWEL_RE.sub(lambda m: m.group(1) + ACUTE, text)


def _normalize_word(word: str) -> str:
    plain = word.replace(ACUTE, "")
    vowel_count = sum(1 for ch in plain if ch in _VOWELS)
    if vowel_count <= 1 or "ё" in plain.lower():
        return plain
    return word


def drop_uninformative_marks(text: str) -> str:
    return _WORD_RE.sub(lambda m: _normalize_word(m.group(0)), text)


class RussianStresser:
    """Wraps ruaccent. Loading is slow - ~16s from disk on this laptop, and
    the first-ever load downloads ~195 MB of models into workdir - so
    load() is separate and meant to run in the background at startup
    (see VoiceboxTTSProvider.warm_up); stress() loads lazily if needed."""

    def __init__(self, model_size: str = DEFAULT_MODEL_SIZE, workdir: str = "models/ruaccent"):
        self._model_size = model_size
        self._workdir = workdir
        self._accentizer = None
        self._lock = threading.Lock()

    def load(self) -> None:
        with self._lock:
            if self._accentizer is None:
                from ruaccent import RUAccent

                accentizer = RUAccent()
                accentizer.load(omograph_model_size=self._model_size, use_dictionary=True, workdir=self._workdir)
                self._accentizer = accentizer

    def stress(self, text: str) -> str:
        self.load()
        # ruaccent silently deletes literal "+" signs ("2+2" came back as
        # "22") - and "плюс" is how a voice should say it anyway.
        safe = text.replace("+", " плюс ")
        return drop_uninformative_marks(plus_marks_to_acute(self._accentizer.process_all(safe)))
