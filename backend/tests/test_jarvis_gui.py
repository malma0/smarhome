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


def test_saved_utterance_also_refreshes_the_dataset_stats():
    ui, calls = _ui()
    ui.stats_source = lambda: {"utterances": 41, "minutes": 3.4, "corrected": 2}
    ui.mark_ready()
    ui.utterance_saved("abc")
    ui.drain()
    assert _events(calls) == [
        {"type": "saved", "id": "abc"},
        {"type": "stats", "minutes": 3.4, "utterances": 41},
    ]


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
    api.correct("utt-1", "включи на нём видео")
    api.wake()

    assert [commands.get_nowait() for _ in range(commands.qsize())] == [
        ("text", "который час?"),
        ("correct", "включи на нём видео", "utt-1"),
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
