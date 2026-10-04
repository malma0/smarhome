"""Jarvis desktop window - the same hands-free voice loop as voice_app.py,
shown as an animated orb and a chat instead of a terminal.

The window is plain HTML/CSS/JS (backend/gui/) inside a native window via
pywebview, which uses the WebView2 runtime built into Windows 11 - no
browser, no tabs, no extra engine to download. The voice loop runs on its
own thread with its own asyncio loop; the window only renders the events it
reports (app.voice_ui) and sends typed messages / corrections / orb clicks
back as commands.

Usage: pythonw jarvis_gui.py (start_jarvis_voice.bat does this). Under
pythonw there's no console, so output goes to backend/jarvis_gui.log.
"""

import asyncio
import json
import queue
import sys
import threading
import time
import traceback
from collections.abc import Callable
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
GUI_DIR = BACKEND_DIR / "gui"
LOG_PATH = BACKEND_DIR / "jarvis_gui.log"

# Mic levels arrive ~12x per second; if the window falls behind, stale ones
# are dropped rather than queued - only the latest loudness matters.
MAX_QUEUED_MIC_EVENTS = 2

TOOL_LABELS = {
    "open_application": "Открыл",
    "write_file": "Записал",
    "read_file": "Прочитал",
    "list_directory": "Посмотрел папку",
    "delete_file": "Удалил",
    "get_home_status": "Посмотрел дом",
    "control_devices": "Дом",
    "set_room_norm": "Норма",
    "run_scenario": "Сценарий",
    "close_application": "Закрыла",
    "search_web": "Поиск",
    "media": "Медиа",
    "desktop": "Компьютер",
    "find_files": "Нашла",
    "type_text": "Напечатала",
    "screen": "Экран",
    "power": "Питание",
    "reminders": "Напоминания",
    "get_weather": "Погода",
}

DEVICE_NAMES = {"light": "свет", "socket": "розетка", "ac": "кондиционер", "heating": "отопление",
                "ventilation": "вентиляция", "water_valve": "кран воды", "gas_valve": "кран газа"}
# (device, action) -> how its state reads: "Кухня: свет включён"
DEVICE_STATES = {
    ("light", "on"): "включён", ("light", "off"): "выключен",
    ("socket", "on"): "включена", ("socket", "off"): "выключена",
    ("ac", "on"): "включён", ("ac", "off"): "выключен",
    ("heating", "on"): "включено", ("heating", "off"): "выключено",
    ("ventilation", "on"): "включена", ("ventilation", "off"): "выключена",
    ("water_valve", "on"): "открыт", ("water_valve", "off"): "закрыт",
    ("gas_valve", "on"): "открыт", ("gas_valve", "off"): "закрыт",
}


def summarize_action(action: dict) -> dict:
    """One short chip per tool call: what happened, or why it failed."""
    tool = action.get("tool", "")
    result = action.get("result") or {}
    tool_input = action.get("input") or {}
    label = TOOL_LABELS.get(tool, tool)
    if isinstance(result, dict) and result.get("error"):
        return {"ok": False, "summary": f"{label}: {result['error']}"[:140]}
    if tool == "get_home_status":
        return {"ok": True, "summary": label}
    if tool == "control_devices":
        done = result.get("done") or []
        rooms = ", ".join(d["room"] for d in done)
        device, action = tool_input.get("device", ""), tool_input.get("action", "")
        summary = f"{rooms}: {DEVICE_NAMES.get(device, device)} {DEVICE_STATES.get((device, action), action)}"
        if result.get("temperature") is not None:
            summary += f", {result['temperature']:g} °C"
        if result.get("brightness_pct") is not None:
            summary += f", {result['brightness_pct']}%"
        return {"ok": True, "summary": summary}
    if tool == "close_application":
        if result.get("closed"):
            return {"ok": True, "summary": f"Закрыла {result['closed']}" + (" (принудительно)" if result.get("forced") else "")}
        return {"ok": True, "summary": f"{result.get('still_running', '')}: работает в фоне".strip()}
    if tool == "search_web":
        return {"ok": True, "summary": f"Поиск: {result.get('searched', '')}"}
    if tool == "media":
        if "volume" in result:
            return {"ok": True, "summary": "Звук выключен" if result.get("muted") else f"Громкость {result['volume']}%"}
        names = {"play_pause": "Пауза / воспроизведение", "next": "Следующий трек", "previous": "Предыдущий трек",
                 "stop": "Стоп"}
        return {"ok": True, "summary": names.get(result.get("done"), label)}
    if tool == "desktop":
        names = {"lock": "Заблокировала компьютер", "minimize_all": "Свернула все окна",
                 "restore_windows": "Вернула окна"}
        return {"ok": True, "summary": names.get(result.get("done"), "Состояние компьютера")}
    if tool == "find_files":
        if result.get("opened"):
            return {"ok": True, "summary": f"Открыла {Path(result['opened']).name}"}
        found = result.get("found") or []
        return {"ok": True, "summary": f"Нашла файлов: {len(found)}" if found else "Ничего не нашла"}
    if tool == "type_text":
        return {"ok": True, "summary": f"Напечатала в «{result.get('into', '')}»"}
    if tool == "screen":
        if result.get("saved"):
            return {"ok": True, "summary": f"Скриншот: {Path(result['saved']).name}"}
        return {"ok": True, "summary": "Посмотрела на экран"}
    if tool == "power":
        if result.get("cancelled"):
            return {"ok": True, "summary": "Выключение отменено"}
        word = "Выключение" if result.get("scheduled") == "shutdown" else "Перезагрузка"
        return {"ok": True, "summary": f"{word} через минуту"}
    if tool == "reminders":
        if result.get("added"):
            added = result["added"]
            what = added["text"] if added["kind"] == "timer" else f"Напомню в {added['at'][-5:]}"
            return {"ok": True, "summary": what}
        if result.get("cancelled"):
            return {"ok": True, "summary": "Отменила: " + ", ".join(r["text"] for r in result["cancelled"])}
        return {"ok": True, "summary": "Посмотрела напоминания"}
    if tool == "get_weather":
        return {"ok": True, "summary": f"Погода: {result.get('place', '')}".strip()}
    if tool == "run_scenario":
        if result.get("ran"):
            return {"ok": True, "summary": f"Сценарий «{result['ran']}»"}
        return {"ok": True, "summary": "Посмотрела сценарии"}
    if tool == "set_room_norm":
        parts = []
        for item in result.get("done") or []:
            if "temperature" in item:
                parts.append(f"{item['room']}: норма {item['temperature']:g} °C")
            if "co2_max" in item:
                parts.append(f"{item['room']}: CO2 до {item['co2_max']:g} ppm")
        return {"ok": True, "summary": "; ".join(parts) or label}
    if tool == "open_application":
        target = result.get("url") or result.get("opened") or tool_input.get("app", "")
        return {"ok": True, "summary": f"Открыл {target}"}
    path = result.get("path") or result.get("deleted") or tool_input.get("path", "")
    name = Path(path).name if path else ""
    return {"ok": True, "summary": f"{label} {name}".strip()}


class WebviewUI:
    """VoiceUI (app.voice_ui) that forwards every event to the page as
    JSON. Events are sent from one sender thread: calls into the window
    block until the page has run them, and the listener thread reporting
    mic levels must never wait on the UI. Anything reported before the
    page has loaded is held and delivered once it has."""

    def __init__(self):
        self._evaluate: Callable[[str], object] | None = None
        self._ready = threading.Event()
        self._outbox: queue.Queue[dict] = queue.Queue()
        self._names: queue.Queue[str] = queue.Queue()
        self.stats_source: Callable[[], dict] | None = None
        threading.Thread(target=self._send_loop, daemon=True).start()

    def attach(self, evaluate: Callable[[str], object]) -> None:
        self._evaluate = evaluate

    def mark_ready(self) -> None:
        self._ready.set()

    def _send_loop(self) -> None:
        while True:
            event = self._outbox.get()
            try:
                self._ready.wait()
                if self._evaluate is not None:
                    payload = json.dumps(event, ensure_ascii=False)
                    self._evaluate(f"window.jarvis && window.jarvis.event({payload})")
            except Exception:  # noqa: BLE001 - a closed/closing window must not kill the sender
                pass
            finally:
                self._outbox.task_done()

    def _send(self, event: dict) -> None:
        if event["type"] == "mic" and self._outbox.qsize() > MAX_QUEUED_MIC_EVENTS:
            return
        self._outbox.put(event)

    def drain(self, timeout: float = 2.0) -> bool:
        """Waits until every queued event was handed to the window (tests)."""
        deadline = time.monotonic() + timeout
        while self._outbox.unfinished_tasks:
            if time.monotonic() > deadline:
                return False
            time.sleep(0.01)
        return True

    # --- VoiceUI ---

    def state(self, state: str, detail: str = "") -> None:
        self._send({"type": "state", "state": state, "detail": detail})

    def user_said(
        self,
        text: str,
        voice: bool,
        utterance_id: str | None = None,
        speaker: str | None = None,
        speaker_sure: bool = True,
    ) -> None:
        self._send(
            {
                "type": "user",
                "text": text,
                "voice": voice,
                "id": utterance_id,
                "speaker": speaker if speaker != "default" else None,
                "speaker_sure": speaker_sure,
            }
        )
        if utterance_id:
            self.push_stats()

    def jarvis_said(self, text: str, actions: list[dict]) -> None:
        self._send({"type": "jarvis", "text": text, "actions": [summarize_action(a) for a in actions]})

    def info(self, text: str) -> None:
        self._send({"type": "info", "text": text.strip()})

    def panel(self, panel_url: str, apk_url: str) -> None:
        """The phone's links, kept in the window (the "Телефон" chip) - a toast is gone too fast."""
        self._send({"type": "panel", "panel": panel_url, "apk": apk_url})

    def resident(self, name: str) -> None:
        self._send({"type": "resident", "name": name})

    def mic_level(self, level: float) -> None:
        self._send({"type": "mic", "level": round(level, 3)})

    def speech_envelope(self, levels: list[float], frame_seconds: float) -> None:
        self._send({"type": "envelope", "levels": levels, "frame_seconds": frame_seconds})

    def voices(self, profiles: list[dict]) -> None:
        self._send({"type": "voices", "profiles": profiles})

    def alert(self, text: str, key: str, active: bool) -> None:
        self._send({"type": "alert", "text": text, "key": key, "active": active})

    def ring(self, text: str, key: str, active: bool) -> None:
        self._send({"type": "ring", "text": text, "key": key, "active": active})

    def enrollment(self, name: str, collected: int, needed: int, status: str, phrase: str = "") -> None:
        self._send({"type": "enroll", "name": name, "collected": collected, "needed": needed, "status": status,
                    "phrase": phrase})

    def ask_name(self, prompt: str, known: list[str] = ()) -> str:
        while not self._names.empty():  # an answer left over from an earlier, abandoned prompt
            self._names.get_nowait()
        self._send({"type": "ask_name", "prompt": prompt, "known": list(known)})
        try:
            return self._names.get(timeout=120).strip()
        except queue.Empty:
            return ""

    # --- extras for the window ---

    def answer_name(self, name: str) -> None:
        self._names.put(name or "")

    def push_stats(self) -> None:
        if self.stats_source is not None:
            stats = self.stats_source()
            self._send({"type": "stats", "minutes": stats["minutes"], "utterances": stats["utterances"]})


class JsApi:
    """What the page can call as window.pywebview.api.<method>(...).
    Everything becomes a command for the voice loop (see
    voice_app.run_hands_free) - nothing runs on pywebview's own threads."""

    def __init__(self, commands: "queue.Queue[tuple]", ui: WebviewUI):
        self._commands = commands
        self._ui = ui

    def send_text(self, text: str) -> None:
        text = (text or "").strip()
        if text:
            self._commands.put(("text", text))

    def correct(self, utterance_id: str | None, text: str, ask: bool = True) -> None:
        """ask=True: also send the corrected text to Jarvis - it answered
        the misheard version. ask=False: only fix the training data."""
        text = (text or "").strip()
        if text:
            self._commands.put(("correct", text, utterance_id or None, bool(ask)))

    def wake(self) -> None:
        self._commands.put(("wake",))

    def stop(self) -> None:
        """"Стоп" on a ringing timer's banner."""
        self._commands.put(("stop",))

    def toggle_mic(self) -> None:
        """The orb: switches the microphone off, or back on and listening."""
        self._commands.put(("toggle_mic",))

    def set_speaker(self, utterance_id: str, name: str) -> None:
        """'Кто говорил?' on a phrase - fixes its speaker label."""
        name = (name or "").strip()
        if utterance_id and name:
            self._commands.put(("speaker", utterance_id, name))

    def start_enroll(self, name: str) -> None:
        name = (name or "").strip()
        if name:
            self._commands.put(("enroll", name))

    def next_enroll(self) -> None:
        """"Дальше" - the phrase on screen was counted, on to the next one."""
        self._commands.put(("enroll_next",))

    def cancel_enroll(self) -> None:
        self._commands.put(("enroll_cancel",))

    def answer_name(self, name: str) -> None:
        self._ui.answer_name(name)


async def _voice_main(ui: WebviewUI, commands: "queue.Queue[tuple]") -> None:
    import voice_app
    from app.config import settings
    from app.voice_ui import SLEEPING

    try:
        session = await voice_app.build_session(ui)
        if session is None:
            ui.state(SLEEPING, "Не задан GROQ_API_KEY в .env")
            return
        if session.utterances is not None:
            ui.stats_source = session.utterances.stats
            ui.push_stats()
        if session.resident_id != "default":
            ui.resident(session.resident_id)
        voice_app.start_danger_watch(ui)
        from app import panel

        if panel.start_in_thread():  # the control panel for the phone / the iPad - only with APP_PIN set
            base = f"http://{panel.home_address() or 'localhost'}:{settings.panel_port}"
            ui.panel(f"{base}/panel/", f"{base}/jarvis.apk" if panel.APK.exists() else "")
        detector = voice_app.build_wake_detector(ui) if settings.voice_mode == "wake" else None
        await voice_app.run_hands_free(session, detector, commands)
    except Exception as exc:  # noqa: BLE001 - show it instead of silently dying behind the window
        traceback.print_exc()
        ui.info(f"Ошибка: {exc}")
        ui.state(SLEEPING, f"Что-то сломалось - подробности в {LOG_PATH.name}")


def _redirect_output_if_windowless() -> None:
    """pythonw has no console: sys.stdout/stderr are None, and libraries
    that print progress (model downloads) would crash writing to them."""
    if sys.stdout is None or sys.stderr is None:
        log = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log
        print(f"\n--- Jarvis window started {time.strftime('%Y-%m-%d %H:%M:%S')} ---")


def _already_running() -> bool:
    """One Jarvis at a time: autostart plus a manual start would mean two
    loops on one microphone. A named Windows mutex lives as long as the
    process that made it - no stale lock file after a crash."""
    if sys.platform != "win32":
        return False
    import ctypes

    kernel32 = ctypes.windll.kernel32
    _already_running.handle = kernel32.CreateMutexW(None, False, "Local\\JarvisVoiceWindow")
    return kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS


def main() -> None:
    _redirect_output_if_windowless()
    if _already_running():
        print("Jarvis уже запущен - второе окно не открываю.")
        return
    import webview

    from app.tts import playback

    ui = WebviewUI()
    commands: queue.Queue[tuple] = queue.Queue()
    window = webview.create_window(
        "Jarvis",
        url=str(GUI_DIR / "index.html"),
        js_api=JsApi(commands, ui),
        width=480,
        height=820,
        min_size=(380, 600),
        background_color="#07090F",
        minimized="--minimized" in sys.argv,  # autostart: in the taskbar, not in your face
    )
    ui.attach(window.evaluate_js)
    window.events.loaded += ui.mark_ready
    playback.set_listener(ui.speech_envelope)

    voice = threading.Thread(target=lambda: asyncio.run(_voice_main(ui, commands)), daemon=True)
    voice.start()
    webview.start()  # blocks until the window is closed

    commands.put(("quit",))
    voice.join(timeout=3)  # lets the loop close the microphone cleanly


if __name__ == "__main__":
    main()
