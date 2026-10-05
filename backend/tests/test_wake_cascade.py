import numpy as np

from wakeword import cascade
from wakeword.verify import names_in


class Model:
    def __init__(self, score):
        self.value = score

    def score(self, audio):
        return self.value


class Verifier:
    def __init__(self, text):
        self.text, self.asked = text, 0

    def check(self, audio):
        self.asked += 1
        return names_in(self.text), self.text, 0.2


def _ready(score, text):
    c = cascade.Cascade()
    c._model, c._verifier = Model(score), Verifier(text)
    c._ready.set()
    return c


def test_the_second_stage_decides_only_what_the_first_flagged():
    audio = np.zeros(16000, dtype=np.int16)
    quiet = _ready(0.3, "Джарвис.")
    assert quiet.check(audio, 1.0)[0] is False and quiet._verifier.asked == 0  # not flagged - no Whisper at all
    named = _ready(0.95, "Джарвис, включи свет.")
    heard, text, note = named.check(audio, 1.0)
    assert heard and text.startswith("Джарвис") and "NAME" in note
    george = _ready(0.95, "Джордж.")
    assert george.check(audio, 1.0)[0] is False  # flagged, and Whisper says it wasn't the name


def test_no_model_no_cascade_and_too_short_is_not_checked():
    c = cascade.Cascade()
    c.error = "no wakeword/models/jarvis.pt"
    c._ready.set()
    assert c.check(np.zeros(16000, dtype=np.int16), 1.0) == (False, "", "")  # Vosk alone, as before
    short = _ready(0.99, "Джарвис.")
    assert short.check(np.zeros(1600, dtype=np.int16), 0.1)[0] is False and short._verifier.asked == 0


def test_whisper_text_counts_as_the_name_only_when_it_is_the_name():
    assert names_in("Джарвис!") and names_in("Эй, джарвиз, слушай")  # one letter off is still it
    assert not names_in("Джордж") and not names_in("Червис") and not names_in("")
