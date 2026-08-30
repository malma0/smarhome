import pytest

from app.db import connect
from app.memory import MemoryStore


@pytest.fixture
def store(tmp_path):
    conn = connect(str(tmp_path / "test.db"))
    return MemoryStore(conn)


def test_persona_mode_defaults_to_warm(store):
    assert store.get_persona_mode() == "warm"


def test_persona_mode_round_trips(store):
    store.set_persona_mode("butler")
    assert store.get_persona_mode() == "butler"


def test_invalid_persona_mode_rejected(store):
    with pytest.raises(ValueError):
        store.set_persona_mode("grumpy")


def test_proactivity_level_defaults_to_quiet(store):
    assert store.get_proactivity_level() == "quiet"


def test_proactivity_level_round_trips(store):
    store.set_proactivity_level("chatty")
    assert store.get_proactivity_level() == "chatty"


def test_invalid_proactivity_level_rejected(store):
    with pytest.raises(ValueError):
        store.set_proactivity_level("very loud")


def test_preference_round_trips(store):
    store.set_preference("ivan", "office_temp_evening", "21")
    assert store.get_preference("ivan", "office_temp_evening") == "21"


def test_preference_missing_returns_default(store):
    assert store.get_preference("ivan", "nope", default="fallback") == "fallback"


def test_preference_update_overwrites(store):
    store.set_preference("ivan", "formality", "0.5")
    store.set_preference("ivan", "formality", "0.7")
    assert store.get_preference("ivan", "formality") == "0.7"


def test_get_preferences_returns_all_for_resident(store):
    store.set_preference("ivan", "formality", "0.5")
    store.set_preference("ivan", "verbosity", "0.3")
    store.set_preference("olga", "formality", "0.9")

    assert store.get_preferences("ivan") == {"formality": "0.5", "verbosity": "0.3"}


def test_ensure_resident_is_idempotent(store):
    store.ensure_resident("ivan", "Ivan")
    store.ensure_resident("ivan", "Ivan")  # should not raise
