"""app.domains.system and the new parts of app.domains.computer - with the
OS side faked: no real keys pressed, volume changed or windows closed."""

import asyncio

from app.domains import computer, system
from app.tools.registry import ToolRegistry, TurnContext


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------- media and volume


class FakeVolume:
    def __init__(self, level=50, muted=False):
        self.level, self.muted = level, muted

    def get(self):
        return self.level, self.muted

    def set(self, level=None, mute=None):
        if level is not None:
            self.level = max(0, min(100, level))
            if level > 0:
                self.muted = False
        if mute is not None:
            self.muted = mute
        return self.level, self.muted


def _media(action, volume=None, pressed=None, **extra):
    volume = volume or FakeVolume()
    pressed = pressed if pressed is not None else []
    return _run(system.media({"action": action, **extra}, TurnContext(), press=pressed.append,
                             get=volume.get, set_=volume.set))


def test_media_keys():
    pressed = []
    assert _media("play_pause", pressed=pressed) == {"done": "play_pause"}
    _media("next", pressed=pressed)
    _media("previous", pressed=pressed)
    assert pressed == [0xB3, 0xB0, 0xB1]


def test_volume_set_up_down_and_mute():
    volume = FakeVolume(55)
    assert _media("volume_set", volume, level=40) == {"volume": 40, "muted": False}
    assert _media("volume_up", volume) == {"volume": 50, "muted": False}
    assert _media("volume_down", volume, level=30) == {"volume": 20, "muted": False}
    assert _media("volume_down", volume, level=50) == {"volume": 0, "muted": False}  # not below 0
    assert _media("mute", volume)["muted"] is True
    assert _media("volume_set", volume, level=30)["muted"] is False  # setting a level unmutes
    assert _media("volume_get", volume) == {"volume": 30, "muted": False}
    assert "needs level" in _media("volume_set", volume)["error"]


def test_no_sound_device_is_an_answer_not_a_crash():
    def broken():
        raise OSError("no endpoint")

    result = _run(system.media({"action": "volume_get"}, TurnContext(), press=None, get=broken, set_=None))
    assert "No sound device" in result["error"]


# --------------------------------------------------------------- desktop


def test_desktop_actions_and_status():
    done = []
    actions = {name: (lambda n=name: done.append(n)) for name in ("lock", "minimize_all", "restore_windows")}
    assert _run(system.desktop({"action": "minimize_all"}, TurnContext(), actions=actions)) == {"done": "minimize_all"}
    assert _run(system.desktop({"action": "lock"}, TurnContext(), actions=actions)) == {"done": "lock"}
    assert done == ["minimize_all", "lock"]
    assert _run(system.desktop({"action": "status"}, TurnContext(), status=lambda: {"cpu_percent": 7})) == {
        "cpu_percent": 7}
    assert "Unknown" in _run(system.desktop({"action": "shutdown"}, TurnContext(), actions=actions))["error"]


# --------------------------------------------------------------- closing apps


class FakeOS:
    def __init__(self, windows, processes, closes=True, dies=True):
        self._windows = windows
        self._processes = processes
        self.closes, self.dies = closes, dies
        self.closed, self.killed = [], []

    def windows(self):
        return [w for w in self._windows if w["hwnd"] not in self.closed or not self.closes]

    def processes(self):
        gone = {w["pid"] for w in self._windows if w["hwnd"] in self.closed and self.closes and self.dies}
        return [p for p in self._processes if p["pid"] not in gone and p["pid"] not in self.killed]

    def closer(self):
        return computer.AppCloser(
            windows=self.windows, processes=self.processes, post_close=self.closed.append,
            window_open=lambda hwnd: not (self.closes and hwnd in self.closed),
            terminate=self.killed.append, own_pids=lambda: {1}, sleep=lambda s: None,
        )


def _w(hwnd, pid, exe, title):
    return {"hwnd": hwnd, "pid": pid, "exe": exe, "title": title}


def test_an_app_closes_like_its_close_button():
    fake = FakeOS([_w(10, 5, "Notepad.exe", "заметки — Блокнот"), _w(11, 6, "chrome.exe", "YouTube")],
                  [{"pid": 5, "exe": "Notepad.exe"}, {"pid": 6, "exe": "chrome.exe"}])
    assert fake.closer().close("блокнот", False) == {"closed": "notepad"}
    assert fake.closed == [10] and fake.killed == []  # only Notepad, gently


def test_one_asking_to_save_is_forced_only_with_a_yes():
    fake = FakeOS([_w(10, 5, "Photoshop.exe", "картинка.psd")], [{"pid": 5, "exe": "Photoshop.exe"}], closes=False)
    result = fake.closer().close("photoshop", False)
    assert "asking to save" in result["error"] and fake.killed == []
    assert fake.closer().close("photoshop", True) == {"closed": "photoshop", "forced": True}
    assert fake.killed == [5]


def test_one_hiding_in_the_tray_is_quit_only_with_a_yes():
    fake = FakeOS([_w(10, 7, "steam.exe", "Steam")], [{"pid": 7, "exe": "steam.exe"}], dies=False)
    result = fake.closer().close("Steam", False)
    assert result["still_running"] == "steam" and fake.killed == []
    tray_only = FakeOS([], [{"pid": 7, "exe": "steam.exe"}])
    assert tray_only.closer().close("steam", False)["still_running"] == "steam"
    assert tray_only.closer().close("steam", True) == {"closed": "steam", "forced": True}


def test_found_by_title_when_the_exe_says_nothing():
    fake = FakeOS([_w(10, 8, "ApplicationFrameHost.exe", "Калькулятор")], [{"pid": 8, "exe": "ApplicationFrameHost.exe"}])
    assert fake.closer().close("калькулятор", False) == {"closed": "applicationframehost"}


def test_what_is_never_closed():
    fake = FakeOS([_w(10, 1, "pythonw.exe", "Jarvis"), _w(11, 9, "csrss.exe", "csrss")],
                  [{"pid": 1, "exe": "pythonw.exe"}, {"pid": 9, "exe": "csrss.exe"}])
    assert "That's me" in fake.closer().close("Джарвис", False)["error"]
    assert "Nothing called" in fake.closer().close("pythonw", False)["error"]  # its own process isn't a target
    assert "part of Windows" in fake.closer().close("csrss", True)["error"]
    assert fake.closed == [] and fake.killed == []


def test_docker_needs_a_yes_first():
    fake = FakeOS([_w(10, 4, "Docker Desktop.exe", "Docker Desktop")], [{"pid": 4, "exe": "Docker Desktop.exe"}])
    assert "Home Assistant" in fake.closer().close("docker", False)["error"] and fake.closed == []
    assert fake.closer().close("docker", True) == {"closed": "docker desktop"}


# --------------------------------------------------------------- search


def test_search_urls(monkeypatch):
    assert computer.search_url("рецепт борща") == "https://yandex.ru/search/?text=%D1%80%D0%B5%D1%86%D0%B5%D0%BF%D1%82+%D0%B1%D0%BE%D1%80%D1%89%D0%B0"
    assert computer.search_url("cats", "youtube") == "https://www.youtube.com/results?search_query=cats"
    opened = []
    monkeypatch.setattr(computer.os, "startfile", opened.append, raising=False)
    result = _run(computer._search_web({"query": "Новосибирск", "site": "maps"}, TurnContext()))
    assert result == {"searched": "Новосибирск", "site": "maps"} and opened[0].startswith("https://yandex.ru/maps/")


def test_all_the_computer_tools_register():
    registry = ToolRegistry()
    computer.register(registry)
    system.register(registry)
    assert [d.name for d in registry.definitions()] == [
        "open_application", "close_application", "search_web", "media", "desktop", "type_text", "screen", "power"]



# --------------------------------------------------------------- typing


def _typing(text, window, press_enter=False):
    sent = []
    result = _run(system.type_text({"text": text, "press_enter": press_enter}, TurnContext(), window=lambda: window,
                                   send=sent.append, own_pids=lambda: {1}))
    return result, sent


def test_typing_goes_to_the_active_window_as_unicode():
    result, sent = _typing("Привет!\nДа", {"pid": 5, "exe": "WINWORD.EXE", "title": "Документ1 - Word"}, press_enter=True)
    assert result == {"typed": 10, "into": "Документ1 - Word"}
    events = sent[0]
    assert (0, ord("П"), system.KEYEVENTF_UNICODE) in events  # the letter itself, whatever the layout
    assert events[-2:] == [(system.VK_RETURN, 0, 0), (system.VK_RETURN, 0, system.KEYEVENTF_KEYUP)]
    assert len(events) == 2 * 10 + 2  # down+up per character (newline as Enter), then Enter


def test_never_into_a_terminal_or_my_own_window():
    result, sent = _typing("rm -rf", {"pid": 5, "exe": "WindowsTerminal.exe", "title": "PowerShell"})
    assert "terminal" in result["error"] and sent == []
    result, sent = _typing("привет", {"pid": 1, "exe": "pythonw.exe", "title": "Jarvis"})
    assert "My own window" in result["error"] and sent == []


# --------------------------------------------------------------- the screen


def test_png_encoding_and_shrinking():
    import zlib

    import numpy as np

    rgb = np.zeros((4, 6, 3), dtype=np.uint8)
    rgb[:, :, 0] = 255
    png = system.png_bytes(rgb)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and png[12:16] == b"IHDR"
    idat = png[png.index(b"IDAT") + 4: png.index(b"IEND") - 8]
    raw = zlib.decompress(idat)
    assert len(raw) == 4 * (1 + 6 * 3) and raw[1:4] == b"\xff\x00\x00"
    big = np.full((1200, 1920, 3), 100, dtype=np.uint8)
    assert system.shrink(big, 1280).shape == (600, 960, 3)
    assert system.shrink(big, 4000) is big


def test_a_screenshot_is_saved_and_a_description_leaves_no_picture(tmp_path):
    captured = []

    def capture(path, max_width=0):
        path.write_bytes(b"png")
        captured.append((path, max_width))
        return path

    saved = _run(system.screen({"action": "screenshot"}, TurnContext(), capture=capture, folder=lambda: tmp_path))
    assert saved["saved"].startswith(str(tmp_path)) and captured[0][1] == 0

    async def vision(image, question):
        assert image == b"png" and "экране" in question
        return "Открыт браузер с YouTube."

    described = _run(system.screen({"action": "describe"}, TurnContext(), capture=capture, vision=vision))
    assert described == {"screen": "Открыт браузер с YouTube."}
    assert captured[1][1] == system.DESCRIBE_WIDTH and not captured[1][0].exists()  # not kept

    async def down(image, question):
        raise RuntimeError("429")

    assert "didn't answer" in _run(system.screen({"action": "describe"}, TurnContext(), capture=capture,
                                                 vision=down))["error"]


# --------------------------------------------------------------- power


def test_shutdown_needs_a_yes_and_can_be_cancelled():
    ran = []

    def run(args):
        ran.append(args)
        return 0

    assert "ask the resident" in _run(system.power({"action": "shutdown"}, TurnContext(), run=run))["error"]
    assert ran == []
    result = _run(system.power({"action": "restart", "confirmed": True}, TurnContext(), run=run))
    assert result == {"scheduled": "restart", "in_seconds": 60, "cancel": "отмени выключение"}
    assert ran[0][:4] == ["shutdown", "/r", "/t", "60"]
    assert _run(system.power({"action": "cancel"}, TurnContext(), run=run)) == {"cancelled": True}
    assert ran[-1] == ["shutdown", "/a"]
    assert "Nothing was scheduled" in _run(system.power({"action": "cancel"}, TurnContext(), run=lambda a: 1116))["error"]
