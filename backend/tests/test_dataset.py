import io
import json
import wave

import numpy as np

from app.dataset import UtteranceLog


def _wav(seconds: float, sample_rate: int = 16000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(np.zeros(int(seconds * sample_rate), dtype=np.int16).tobytes())
    return buffer.getvalue()


def _records(root) -> list[dict]:
    return [json.loads(line) for line in (root / "metadata.jsonl").read_text(encoding="utf-8").splitlines()]


def test_log_writes_the_audio_and_an_audiofolder_metadata_line(tmp_path):
    """file_name relative to the dataset folder is exactly what Hugging
    Face's audiofolder loader expects - no export step needed to train."""
    log = UtteranceLog(tmp_path)
    wav = _wav(1.5)

    utterance_id = log.log(wav_bytes=wav, resident_id="Матвей", transcript="включи свет", response="Включаю.")

    [record] = _records(tmp_path)
    assert record["id"] == utterance_id
    assert (tmp_path / record["file_name"]).read_bytes() == wav
    assert record["file_name"].startswith("audio/")
    assert record["resident_id"] == "Матвей"
    assert record["transcript"] == "включи свет"
    assert record["corrected_text"] is None
    assert record["response"] == "Включаю."
    assert record["duration_seconds"] == 1.5


def test_cyrillic_is_stored_readable_not_escaped(tmp_path):
    log = UtteranceLog(tmp_path)
    log.log(wav_bytes=_wav(0.5), resident_id="Матвей", transcript="привет")

    assert "привет" in (tmp_path / "metadata.jsonl").read_text(encoding="utf-8")


def test_failed_transcription_is_still_logged_for_manual_correction(tmp_path):
    log = UtteranceLog(tmp_path)
    log.log(wav_bytes=_wav(0.5), resident_id="default", transcript=None)

    [record] = _records(tmp_path)
    assert record["transcript"] is None
    assert record["response"] is None


def test_correct_sets_corrected_text_on_that_utterance_only(tmp_path):
    log = UtteranceLog(tmp_path)
    first = log.log(wav_bytes=_wav(0.5), resident_id="default", transcript="на нём видео")
    log.log(wav_bytes=_wav(0.5), resident_id="default", transcript="как тебе дела")

    assert log.correct(first, "включи на нём видео") is True

    records = _records(tmp_path)
    assert records[0]["corrected_text"] == "включи на нём видео"
    assert records[0]["transcript"] == "на нём видео"  # the original mistake is kept too
    assert records[1]["corrected_text"] is None


def test_response_can_be_filled_in_after_logging(tmp_path):
    log = UtteranceLog(tmp_path)
    utterance_id = log.log(wav_bytes=_wav(0.5), resident_id="default", transcript="сколько времени")

    assert log.set_response(utterance_id, "Сейчас 15:30.") is True

    [record] = _records(tmp_path)
    assert record["response"] == "Сейчас 15:30."
    assert record["transcript"] == "сколько времени"


def test_correct_unknown_id_returns_false_and_changes_nothing(tmp_path):
    log = UtteranceLog(tmp_path)
    log.log(wav_bytes=_wav(0.5), resident_id="default", transcript="привет")
    before = (tmp_path / "metadata.jsonl").read_text(encoding="utf-8")

    assert log.correct("no-such-id", "что угодно") is False
    assert (tmp_path / "metadata.jsonl").read_text(encoding="utf-8") == before


def test_correct_on_an_empty_dataset_returns_false(tmp_path):
    assert UtteranceLog(tmp_path).correct("anything", "text") is False


def test_stats_counts_utterances_minutes_and_corrections(tmp_path):
    log = UtteranceLog(tmp_path)
    assert log.stats() == {"utterances": 0, "minutes": 0.0, "corrected": 0}

    first = log.log(wav_bytes=_wav(30), resident_id="default", transcript="раз")
    log.log(wav_bytes=_wav(60), resident_id="default", transcript="два")
    log.correct(first, "раз!")

    assert log.stats() == {"utterances": 2, "minutes": 1.5, "corrected": 1}
