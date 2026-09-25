from app.hands_free import CUE, IGNORE, PROCESS, HandsFreeState
from app.wake_word import NONE, WAKE_ONLY, WAKE_WITH_COMMAND


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _state():
    clock = _Clock()
    return HandsFreeState(wake_listen_seconds=8, follow_up_seconds=10, clock=clock), clock


def test_asleep_by_default_and_ignores_phrases_without_the_name():
    state, _ = _state()
    assert not state.is_awake()
    assert state.on_phrase(NONE) == IGNORE
    assert not state.is_awake()


def test_name_with_a_command_is_handled_immediately():
    state, _ = _state()
    assert state.on_phrase(WAKE_WITH_COMMAND) == PROCESS


def test_name_alone_plays_a_cue_and_listens_for_the_command_without_the_name():
    state, clock = _state()
    assert state.on_phrase(WAKE_ONLY) == CUE
    clock.now += 5
    assert state.is_awake()
    assert state.on_phrase(None) == PROCESS  # "включи свет" - no name needed now


def test_listening_after_the_name_times_out_back_to_sleep():
    state, clock = _state()
    state.on_phrase(WAKE_ONLY)
    clock.now += 8.1
    assert not state.is_awake()
    assert state.on_phrase(NONE) == IGNORE


def test_follow_up_window_after_a_reply_allows_continuing_without_the_name():
    state, clock = _state()
    state.on_phrase(WAKE_WITH_COMMAND)
    clock.now += 45  # a slow spoken reply
    state.after_reply()
    clock.now += 9
    assert state.on_phrase(None) == PROCESS


def test_follow_up_window_is_counted_from_the_end_of_the_reply():
    state, clock = _state()
    state.on_phrase(WAKE_ONLY)
    clock.now += 1
    state.on_phrase(None)  # command - handling starts
    clock.now += 60  # reply takes a minute
    assert not state.is_awake()  # the old window didn't run on during it
    state.after_reply()
    assert state.is_awake()


def test_force_wake_acts_like_hearing_the_bare_name():
    state, clock = _state()
    state.force_wake()
    clock.now += 7
    assert state.on_phrase(None) == PROCESS
    state.force_wake()
    clock.now += 8.1
    assert not state.is_awake()


def test_falls_asleep_after_the_follow_up_window():
    state, clock = _state()
    state.after_reply()
    clock.now += 10.1
    assert not state.is_awake()
    assert state.on_phrase(NONE) == IGNORE



def test_stop_phrases():
    from app.hands_free import is_stop_phrase

    for text in ("стоп", "Стоп.", "Джарвис, стоп!", "хватит", "тихо", "замолчи", "стоп стоп", "всё, хватит"):
        assert is_stop_phrase(text), text
    for text in ("", None, "стоп музыку включи", "не останавливайся", "включи свет", "хватит на сегодня работать"):
        assert not is_stop_phrase(text), text



def test_the_name_heard_over_an_answer():
    from app.hands_free import name_heard

    wake = ["джарвис", "джервис"]
    # as measured: the reply and the command came out as one long phrase
    assert name_heard("у самого синего моря джарвис рик ловил не водам рыбу", wake)
    assert name_heard("Джервис, включи свет", wake)
    assert not name_heard("стоп хватит старик ловил рыбу", wake)
    assert not name_heard(None, wake)
