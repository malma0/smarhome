from types import SimpleNamespace

from app.tts.sapi import pick_voice_id


def voice(id_: str, languages: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=id_, languages=languages or [])


def test_matches_by_language_in_voice_id():
    voices = [voice("HKEY...\\TTS_MS_EN-US_ZIRA"), voice("HKEY...\\TTS_MS_RU-RU_IRINA")]
    assert pick_voice_id(voices, "ru") == "HKEY...\\TTS_MS_RU-RU_IRINA"


def test_matches_by_declared_languages_when_id_does_not_hint_it():
    voices = [voice("some-opaque-id-1", languages=["ru-RU"])]
    assert pick_voice_id(voices, "ru") == "some-opaque-id-1"


def test_returns_none_when_nothing_matches():
    voices = [voice("HKEY...\\TTS_MS_EN-US_ZIRA")]
    assert pick_voice_id(voices, "ru") is None


def test_returns_first_match_when_multiple_qualify():
    voices = [voice("id-ru-1"), voice("id-ru-2")]
    assert pick_voice_id(voices, "ru") == "id-ru-1"
