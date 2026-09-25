"""The window's Python side without an actual window: WebviewUI is given a
fake evaluate() that records the JavaScript it would have run. pywebview
itself is only imported inside jarvis_gui.main(), so these run without it."""

import json
import queue
import threading

from jarvis_gui import JsApi, WebviewUI, summarize_action


def _events(calls):
    prefix = "window.jarvis && window.jarvis.event("
    return [json.loads(c[len(prefix) : -1]) for c in calls]


def _ui():
    calls = []
    ui = WebviewUI()
    ui.attach(calls.append)
    return ui, calls


def test_events_wait_for_the_page_then_arrive_in_order():
    ui, calls = _ui()
    ui.state("sleeping", "Скажи «Джарвис»")
    ui.user_said("привет", voice=True)
    assert calls == []  # page not loaded yet - held, not lost

    ui.mark_ready()
    assert ui.drain()
    assert [e["type"] for e in _events(calls)] == ["state", "user"]
    assert _events(calls)[0]["detail"] == "Скажи «Джарвис»"


def test_cyrillic_reaches_the_page_as_real_text():
    ui, calls = _ui()
    ui.mark_ready()
    ui.jarvis_said("Включаю свет.", [])
    ui.drain()
    assert "Включаю свет." in calls[0]  # ensure_ascii=False - no \u escapes to garble


def test_mic_levels_are_dropped_when_the_window_falls_behind():
    ui, calls = _ui()  # not ready: everything piles up in the outbox
    for _ in range(50):
        ui.mic_level(0.5)
    ui.mark_ready()
    ui.drain()
    assert len(calls) <= 5  # a handful got through, the other ~45 stale ones were dropped


def test_a_saved_voice_phrase_carries_its_id_and_refreshes_the_stats():
    ui, calls = _ui()
    ui.stats_source = lambda: {"utterances": 41, "minutes": 3.4, "corrected": 2}
    ui.mark_ready()
    ui.user_said("включи свет", voice=True, utterance_id="abc", speaker="Матвей", speaker_sure=False)
    ui.user_said("который час", voice=False)  # typed - nothing saved, no stats refresh
    ui.drain()
    assert _events(calls) == [
        {"type": "user", "text": "включи свет", "voice": True, "id": "abc", "speaker": "Матвей", "speaker_sure": False},
        {"type": "stats", "minutes": 3.4, "utterances": 41},
        {"type": "user", "text": "который час", "voice": False, "id": None, "speaker": None, "speaker_sure": True},
    ]


def test_the_page_can_fix_who_said_a_phrase():
    commands = queue.Queue()
    ui, _ = _ui()
    api = JsApi(commands, ui)
    api.set_speaker("utt-1", " Эля ")
    api.set_speaker("", "Эля")  # no phrase id - ignored
    assert [commands.get_nowait() for _ in range(commands.qsize())] == [("speaker", "utt-1", "Эля")]


def test_ask_name_blocks_until_the_page_answers():
    ui, calls = _ui()
    ui.mark_ready()
    answer = []
    t = threading.Thread(target=lambda: answer.append(ui.ask_name("Как тебя зовут?")))
    t.start()
    ui.drain()
    assert _events(calls)[-1]["type"] == "ask_name"

    ui.answer_name(" Матвей ")
    t.join(timeout=2)
    assert answer == ["Матвей"]


def test_page_actions_become_commands_for_the_voice_loop():
    commands = queue.Queue()
    ui, _ = _ui()
    api = JsApi(commands, ui)

    api.send_text("  который час?  ")
    api.send_text("   ")  # blank - ignored
    api.correct("utt-1", "включи на нём видео")  # default: fix and ask again
    api.correct("utt-2", "только данные", False)
    api.wake()

    assert [commands.get_nowait() for _ in range(commands.qsize())] == [
        ("text", "который час?"),
        ("correct", "включи на нём видео", "utt-1", True),
        ("correct", "только данные", "utt-2", False),
        ("wake",),
    ]


def test_action_summaries():
    assert summarize_action(
        {"tool": "open_application", "input": {"app": "notepad"}, "result": {"ok": True, "opened": "notepad"}}
    ) == {"ok": True, "summary": "Открыл notepad"}
    assert summarize_action(
        {"tool": "open_application", "input": {"app": "browser"}, "result": {"ok": True, "opened": "browser", "url": "https://youtube.com"}}
    ) == {"ok": True, "summary": "Открыл https://youtube.com"}
    assert summarize_action(
        {"tool": "write_file", "input": {"path": "hello.txt"}, "result": {"ok": True, "path": r"C:\x\hello.txt"}}
    ) == {"ok": True, "summary": "Записал hello.txt"}
    failed = summarize_action({"tool": "delete_file", "input": {"path": "a.txt"}, "result": {"error": "'a.txt' does not exist."}})
    assert failed["ok"] is False and "does not exist" in failed["summary"]


def test_home_actions_read_as_the_new_state():
    assert summarize_action(
        {"tool": "control_devices", "input": {"room": "кухне", "device": "light", "action": "on", "brightness_pct": 40},
         "result": {"done": [{"room": "Кухня", "device": "light", "action": "on"}], "brightness_pct": 40}}
    ) == {"ok": True, "summary": "Кухня: свет включён, 40%"}
    assert summarize_action(
        {"tool": "control_devices", "input": {"room": "кабинет", "device": "ac", "action": "on", "temperature": 22},
         "result": {"done": [{"room": "Кабинет", "device": "ac", "action": "on"}], "temperature": 22.0}}
    ) == {"ok": True, "summary": "Кабинет: кондиционер включён, 22 °C"}
    assert summarize_action(
        {"tool": "control_devices", "input": {"room": "all", "device": "socket", "action": "off"},
         "result": {"done": [{"room": "Зал", "device": "socket", "action": "off"},
                             {"room": "Кухня", "device": "socket", "action": "off"}]}}
    ) == {"ok": True, "summary": "Зал, Кухня: розетка выключена"}
    assert summarize_action({"tool": "get_home_status", "input": {}, "result": {"rooms": {}}}) == {
        "ok": True, "summary": "Посмотрел дом"}


def test_norm_changes_read_as_the_new_norm():
    assert summarize_action(
        {"tool": "set_room_norm", "input": {"room": "спальня", "temperature": 23},
         "result": {"done": [{"room": "Спальня", "temperature": 23.0}]}}
    ) == {"ok": True, "summary": "Спальня: норма 23 °C"}
    assert summarize_action(
        {"tool": "control_devices", "input": {"room": "кухня", "device": "ventilation", "action": "on"},
         "result": {"done": [{"room": "Кухня", "device": "ventilation", "action": "on"}]}}
    ) == {"ok": True, "summary": "Кухня: вентиляция включена"}


def test_a_danger_reaches_the_window_with_its_key():
    ui, calls = _ui()
    ui.mark_ready()
    ui.alert("Внимание! Дым: кухня!", "smoke:Кухня", True)
    assert ui.drain()
    assert _events(calls) == [{"type": "alert", "text": "Внимание! Дым: кухня!", "key": "smoke:Кухня", "active": True}]



def test_a_scenario_reads_as_its_name():
    assert summarize_action(
        {"tool": "run_scenario", "input": {"name": "я ухожу"}, "result": {"ran": "Я ушёл", "does": "..."}}
    ) == {"ok": True, "summary": "Сценарий «Я ушёл»"}
