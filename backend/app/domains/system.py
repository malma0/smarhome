"""The computer itself, beyond opening apps (app.domains.computer): media
keys, the volume, the desktop (lock, minimize everything, bring it back),
how the machine is doing (battery, CPU, memory, disks), typing into the
active window, the screen (a screenshot; "что у меня на экране?") and
shutting down / restarting.

Windows only, no extra installs: media keys are key presses (keybd_event),
the volume is the Core Audio endpoint behind the tray's speaker icon
(IAudioEndpointVolume, through comtypes - already here for the offline
voice), the rest is plain Win32 through ctypes and pywin32. COM calls run
on a worker thread with COM initialized there, never on the voice loop.
"""

import asyncio
import base64
import ctypes
import os
import shutil
import struct
import subprocess
import time
import zlib
from ctypes import POINTER, byref, c_float, c_void_p, cast
from ctypes.wintypes import BOOL, DWORD, UINT
from datetime import datetime
from pathlib import Path

import numpy as np

from app.tools.registry import Tool, ToolRegistry, TurnContext

# --------------------------------------------------------------- media keys

KEYEVENTF_KEYUP = 0x0002
MEDIA_KEYS = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2}


def _press(vk: int) -> None:
    user32 = ctypes.windll.user32
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


# --------------------------------------------------------------- the volume


def _endpoint_volume():
    """The default speakers' IAudioEndpointVolume. Call on a thread where
    COM is initialized (see _com)."""
    from comtypes import CLSCTX_ALL, COMMETHOD, GUID, HRESULT, IUnknown, CoCreateInstance

    class IAudioEndpointVolume(IUnknown):
        _iid_ = GUID("{5CDF2C82-841E-4546-9722-0CF74078229A}")
        _methods_ = [  # vtable order - only the first ones are used, the rest can be left off
            COMMETHOD([], HRESULT, "RegisterControlChangeNotify", (["in"], c_void_p, "pNotify")),
            COMMETHOD([], HRESULT, "UnregisterControlChangeNotify", (["in"], c_void_p, "pNotify")),
            COMMETHOD([], HRESULT, "GetChannelCount", (["out"], POINTER(UINT), "pnChannelCount")),
            COMMETHOD([], HRESULT, "SetMasterVolumeLevel", (["in"], c_float, "fLevelDB"),
                      (["in"], POINTER(GUID), "pguidEventContext")),
            COMMETHOD([], HRESULT, "SetMasterVolumeLevelScalar", (["in"], c_float, "fLevel"),
                      (["in"], POINTER(GUID), "pguidEventContext")),
            COMMETHOD([], HRESULT, "GetMasterVolumeLevel", (["out"], POINTER(c_float), "pfLevelDB")),
            COMMETHOD([], HRESULT, "GetMasterVolumeLevelScalar", (["out"], POINTER(c_float), "pfLevel")),
            COMMETHOD([], HRESULT, "SetChannelVolumeLevel", (["in"], UINT, "nChannel"), (["in"], c_float, "fLevelDB"),
                      (["in"], POINTER(GUID), "pguidEventContext")),
            COMMETHOD([], HRESULT, "SetChannelVolumeLevelScalar", (["in"], UINT, "nChannel"),
                      (["in"], c_float, "fLevel"), (["in"], POINTER(GUID), "pguidEventContext")),
            COMMETHOD([], HRESULT, "GetChannelVolumeLevel", (["in"], UINT, "nChannel"),
                      (["out"], POINTER(c_float), "pfLevelDB")),
            COMMETHOD([], HRESULT, "GetChannelVolumeLevelScalar", (["in"], UINT, "nChannel"),
                      (["out"], POINTER(c_float), "pfLevel")),
            COMMETHOD([], HRESULT, "SetMute", (["in"], BOOL, "bMute"), (["in"], POINTER(GUID), "pguidEventContext")),
            COMMETHOD([], HRESULT, "GetMute", (["out"], POINTER(BOOL), "pbMute")),
        ]

    class IMMDevice(IUnknown):
        _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
        _methods_ = [
            COMMETHOD([], HRESULT, "Activate", (["in"], POINTER(GUID), "iid"), (["in"], DWORD, "dwClsCtx"),
                      (["in"], c_void_p, "pActivationParams"), (["out"], POINTER(c_void_p), "ppInterface")),
        ]

    class IMMDeviceEnumerator(IUnknown):
        _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
        _methods_ = [
            COMMETHOD([], HRESULT, "EnumAudioEndpoints", (["in"], DWORD, "dataFlow"), (["in"], DWORD, "dwStateMask"),
                      (["out"], POINTER(c_void_p), "ppDevices")),
            COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint", (["in"], DWORD, "dataFlow"), (["in"], DWORD, "role"),
                      (["out"], POINTER(POINTER(IMMDevice)), "ppEndpoint")),
        ]

    enumerator = CoCreateInstance(GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}"), IMMDeviceEnumerator, CLSCTX_ALL)
    speakers = enumerator.GetDefaultAudioEndpoint(0, 1)  # eRender, eMultimedia
    pointer = speakers.Activate(byref(IAudioEndpointVolume._iid_), CLSCTX_ALL, None)
    return cast(pointer, POINTER(IAudioEndpointVolume))


def _com(fn):
    """Runs fn on this (worker) thread with COM initialized for it."""
    import comtypes

    comtypes.CoInitialize()
    try:
        return fn()
    finally:
        comtypes.CoUninitialize()


def get_volume() -> tuple[int, bool]:
    def read():
        volume = _endpoint_volume()
        return round(volume.GetMasterVolumeLevelScalar() * 100), bool(volume.GetMute())

    return _com(read)


def set_volume(level: int | None = None, mute: bool | None = None) -> tuple[int, bool]:
    def write():
        volume = _endpoint_volume()
        if level is not None:
            volume.SetMasterVolumeLevelScalar(max(0, min(100, level)) / 100, None)
            if level > 0:
                volume.SetMute(False, None)  # "сделай 40%" while muted should be heard
        if mute is not None:
            volume.SetMute(mute, None)
        return round(volume.GetMasterVolumeLevelScalar() * 100), bool(volume.GetMute())

    return _com(write)


VOLUME_STEP = 10


async def media(tool_input: dict, ctx: TurnContext, *, press=_press, get=get_volume, set_=set_volume) -> dict:
    action = tool_input.get("action")
    if action in MEDIA_KEYS:
        press(MEDIA_KEYS[action])
        return {"done": action}
    try:
        if action == "volume_get":
            level, muted = await asyncio.to_thread(get)
        elif action == "volume_set":
            if tool_input.get("level") is None:
                return {"error": "volume_set needs level (0-100)."}
            level, muted = await asyncio.to_thread(set_, int(tool_input["level"]), None)
        elif action in ("volume_up", "volume_down"):
            step = int(tool_input.get("level") or VOLUME_STEP)
            current, _ = await asyncio.to_thread(get)
            target = current + step if action == "volume_up" else current - step
            level, muted = await asyncio.to_thread(set_, target, None)
        elif action in ("mute", "unmute"):
            level, muted = await asyncio.to_thread(set_, None, action == "mute")
        else:
            return {"error": f"Unknown action {action!r}."}
    except OSError as exc:  # COM errors are OSError subclasses
        return {"error": f"No sound device to control: {exc}"}
    return {"volume": level, "muted": muted}


# --------------------------------------------------------------- the desktop


def _shell_windows(method: str) -> None:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        getattr(win32com.client.Dispatch("Shell.Application"), method)()
    finally:
        pythoncom.CoUninitialize()


class _PowerStatus(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_byte), ("BatteryFlag", ctypes.c_byte),
                ("BatteryLifePercent", ctypes.c_byte), ("SystemStatusFlag", ctypes.c_byte),
                ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]


class _MemoryStatus(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def _cpu_percent(interval: float = 0.4) -> int:
    def times():
        idle, kernel, user = (ctypes.c_ulonglong() for _ in range(3))
        ctypes.windll.kernel32.GetSystemTimes(byref(idle), byref(kernel), byref(user))
        return idle.value, kernel.value + user.value  # kernel time includes idle

    idle1, total1 = times()
    time.sleep(interval)
    idle2, total2 = times()
    busy = (total2 - total1) - (idle2 - idle1)
    return round(100 * busy / (total2 - total1)) if total2 > total1 else 0


def _gb(n: float) -> str:
    return f"{n / 1024 ** 3:.1f} ГБ"


def read_status() -> dict:
    kernel32 = ctypes.windll.kernel32
    status: dict = {}

    power = _PowerStatus()
    if kernel32.GetSystemPowerStatus(byref(power)) and power.BatteryFlag != -128:  # 128: no battery
        percent = power.BatteryLifePercent & 0xFF
        battery = {"percent": percent, "charging": power.ACLineStatus == 1}
        seconds_left = power.BatteryLifeTime & 0xFFFFFFFF
        if seconds_left != 0xFFFFFFFF and not battery["charging"]:
            battery["time_left"] = f"{seconds_left // 3600} ч {seconds_left % 3600 // 60} мин"
        status["battery"] = battery

    status["cpu_percent"] = _cpu_percent()

    memory = _MemoryStatus()
    memory.dwLength = ctypes.sizeof(_MemoryStatus)
    kernel32.GlobalMemoryStatusEx(byref(memory))
    status["memory"] = {"used_percent": memory.dwMemoryLoad, "free": _gb(memory.ullAvailPhys),
                        "total": _gb(memory.ullTotalPhys)}

    drives = []
    mask = kernel32.GetLogicalDrives()
    for i in range(26):
        root = f"{chr(65 + i)}:\\"
        if mask >> i & 1 and kernel32.GetDriveTypeW(root) == 3:  # DRIVE_FIXED
            usage = shutil.disk_usage(root)
            drives.append({"drive": root[:2], "free": _gb(usage.free), "total": _gb(usage.total)})
    status["disks"] = drives

    uptime = kernel32.GetTickCount64() // 1000
    status["uptime"] = f"{uptime // 86400} д {uptime % 86400 // 3600} ч {uptime % 3600 // 60} мин"
    try:
        level, muted = get_volume()
        status["volume"] = {"level": level, "muted": muted}
    except OSError:
        pass
    return status


DESKTOP_ACTIONS = {
    "lock": lambda: ctypes.windll.user32.LockWorkStation(),
    "minimize_all": lambda: _shell_windows("MinimizeAll"),
    "restore_windows": lambda: _shell_windows("UndoMinimizeALL"),
}


async def desktop(tool_input: dict, ctx: TurnContext, *, actions=DESKTOP_ACTIONS, status=read_status) -> dict:
    action = tool_input.get("action")
    if action == "status":
        return await asyncio.to_thread(status)
    if action not in actions:
        return {"error": f"Unknown action {action!r}."}
    await asyncio.to_thread(actions[action])
    return {"done": action}


# --------------------------------------------------------------- typing (dictation)
#
# "Напиши в текущее окно: ..." - the text goes to whatever window is active,
# character by character as Unicode, so the keyboard layout doesn't matter.
# Never into a terminal: text plus Enter there runs as a command. Never into
# Jarvis's own window: then the resident hasn't picked where to type yet.

INPUT_KEYBOARD = 1
KEYEVENTF_UNICODE = 0x0004
VK_RETURN = 0x0D
TERMINAL_EXES = {"cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe", "wt.exe", "bash.exe",
                 "mintty.exe", "wsl.exe", "conhost.exe", "openconsole.exe", "putty.exe", "alacritty.exe",
                 "wezterm-gui.exe", "git-bash.exe"}


class _KeyboardInput(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t)]


class _InputUnion(ctypes.Union):
    _fields_ = [("ki", _KeyboardInput), ("padding", ctypes.c_byte * 32)]  # the union's biggest member (mouse)


class _Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("u", _InputUnion)]


def _key_events(text: str, press_enter: bool) -> list:
    events = []
    for ch in text.replace("\r\n", "\n"):
        if ch == "\n":
            codes = [(VK_RETURN, 0, 0)]
        else:
            data = ch.encode("utf-16-le")
            codes = [(0, int.from_bytes(data[i:i + 2], "little"), KEYEVENTF_UNICODE) for i in range(0, len(data), 2)]
        for vk, scan, flags in codes:
            events.append((vk, scan, flags))
            events.append((vk, scan, flags | KEYEVENTF_KEYUP))
    if press_enter:
        events += [(VK_RETURN, 0, 0), (VK_RETURN, 0, KEYEVENTF_KEYUP)]
    return events


def _send_keys(events: list) -> None:
    inputs = (_Input * len(events))()
    for i, (vk, scan, flags) in enumerate(events):
        inputs[i].type = INPUT_KEYBOARD
        inputs[i].u.ki = _KeyboardInput(vk, scan, flags, 0, 0)
    ctypes.windll.user32.SendInput(len(events), inputs, ctypes.sizeof(_Input))


def foreground_window() -> dict:
    import win32gui
    import win32process

    from app.domains.computer import _exe_of

    hwnd = win32gui.GetForegroundWindow()
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    return {"pid": pid, "exe": _exe_of(pid), "title": win32gui.GetWindowText(hwnd)}


def _own_pids() -> set[int]:
    return {os.getpid(), os.getppid()}


async def type_text(tool_input: dict, ctx: TurnContext, *, window=foreground_window, send=_send_keys,
                    own_pids=_own_pids) -> dict:
    text = tool_input.get("text") or ""
    if not text.strip():
        return {"error": "Nothing to type."}
    active = window()
    if active["pid"] in own_pids():
        return {"error": ("My own window is the active one - the resident should click where the text goes "
                          "first, then ask by voice.")}
    if active["exe"].lower() in TERMINAL_EXES:
        return {"error": "The active window is a terminal - typing there could run commands, so no."}
    send(_key_events(text, bool(tool_input.get("press_enter"))))
    return {"typed": len(text), "into": active["title"] or active["exe"]}


# --------------------------------------------------------------- the screen
#
# A screenshot is saved to Pictures\Screenshots. "Что у меня на экране?"
# sends a shrunk picture of the screen to a model that can see (VISION_MODEL
# - qwen/qwen3.8-27b on Groq, the one on this key that takes images) - so it
# leaves the computer, and only when the resident asks; it isn't kept.

DESCRIBE_WIDTH = 1280
VISION_TIMEOUT_SECONDS = 60
# Per-monitor DPI awareness for this thread only: the real pixels of every
# monitor, not a scaled-down part of them, and the rest of Jarvis untouched.
_DPI_PER_MONITOR_V2 = ctypes.c_void_p(-4)


def grab_screen() -> np.ndarray:
    """The whole desktop (all monitors) as an RGB array - plain GDI, in this
    process. (Asked of PowerShell instead, Windows Defender blocked it as
    malicious - screen grabbing from a script is what malware does.)"""
    import win32con
    import win32gui
    import win32ui

    user32 = ctypes.windll.user32
    previous = user32.SetThreadDpiAwarenessContext(_DPI_PER_MONITOR_V2)
    try:
        left, top = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)  # SM_X/YVIRTUALSCREEN
        width, height = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
        desktop = win32gui.GetDesktopWindow()
        window_dc = win32gui.GetWindowDC(desktop)
        source = win32ui.CreateDCFromHandle(window_dc)
        memory = source.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        try:
            bitmap.CreateCompatibleBitmap(source, width, height)
            memory.SelectObject(bitmap)
            memory.BitBlt((0, 0), (width, height), source, (left, top), win32con.SRCCOPY)
            bgra = np.frombuffer(bitmap.GetBitmapBits(True), dtype=np.uint8).reshape(height, width, 4)
            return bgra[:, :, 2::-1].copy()  # BGRA -> RGB
        finally:
            win32gui.DeleteObject(bitmap.GetHandle())
            memory.DeleteDC()
            source.DeleteDC()
            win32gui.ReleaseDC(desktop, window_dc)
    finally:
        if previous:
            user32.SetThreadDpiAwarenessContext(previous)


def shrink(rgb: np.ndarray, max_width: int) -> np.ndarray:
    """Box-average down by a whole factor to at most max_width - text stays
    readable, unlike skipping pixels."""
    factor = -(-rgb.shape[1] // max_width)  # ceil
    if factor <= 1:
        return rgb
    h, w = rgb.shape[0] // factor * factor, rgb.shape[1] // factor * factor
    blocks = rgb[:h, :w].reshape(h // factor, factor, w // factor, factor, 3)
    return blocks.mean(axis=(1, 3)).astype(np.uint8)


def png_bytes(rgb: np.ndarray) -> bytes:
    """A PNG from an RGB array - zlib and a few chunks, no imaging library."""
    height, width = rgb.shape[:2]
    rows = np.hstack([np.zeros((height, 1), dtype=np.uint8), rgb.reshape(height, width * 3)])  # filter 0

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows.tobytes(), 6))
            + chunk(b"IEND", b""))


def capture_screen(path: Path, max_width: int = 0) -> Path:
    """Saves the screen as PNG - full size, or shrunk to max_width for a model."""
    rgb = grab_screen()
    if max_width:
        rgb = shrink(rgb, max_width)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png_bytes(rgb))
    return path


def screenshots_folder() -> Path:
    return Path.home() / "Pictures" / "Screenshots"


async def ask_vision_model(image: bytes, question: str) -> str:
    from app.config import settings
    from app.http_client import shared_client

    response = await shared_client().post(
        f"{settings.groq_base_url}/chat/completions",
        headers={"Authorization": f"Bearer {settings.groq_api_key}"},
        timeout=VISION_TIMEOUT_SECONDS,
        json={
            "model": settings.vision_model,
            "max_tokens": 400,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(image).decode()}},
            ]}],
        },
    )
    if response.status_code != 200:
        raise RuntimeError(f"the vision model answered {response.status_code}: {response.text[:200]}")
    return (response.json()["choices"][0]["message"].get("content") or "").strip()


async def screen(tool_input: dict, ctx: TurnContext, *, capture=capture_screen, vision=ask_vision_model,
                 folder=screenshots_folder) -> dict:
    action = tool_input.get("action") or "screenshot"
    if action == "screenshot":
        path = folder() / f"Jarvis {datetime.now():%Y-%m-%d %H-%M-%S}.png"
        try:
            await asyncio.to_thread(capture, path)
        except Exception as exc:  # noqa: BLE001 - GDI/pywin32 errors come in many types
            return {"error": f"Couldn't take the screenshot: {exc}"}
        return {"saved": str(path)}
    if action != "describe":
        return {"error": f"Unknown action {action!r}."}
    question = (tool_input.get("question") or "").strip() or (
        "Опиши по-русски, коротко, что сейчас на экране: какие программы открыты и что в них видно.")
    path = Path(os.environ.get("TEMP", ".")) / "jarvis_screen.png"
    try:
        await asyncio.to_thread(capture, path, DESCRIBE_WIDTH)
        image = path.read_bytes()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Couldn't see the screen: {exc}"}
    finally:
        path.unlink(missing_ok=True)  # the picture itself isn't kept
    try:
        return {"screen": await vision(image, question)}
    except Exception as exc:  # noqa: BLE001 - network, rate limit, the model's own error
        return {"error": f"The model that looks at pictures didn't answer: {exc}"}


# --------------------------------------------------------------- power
#
# Shutting down or restarting always takes the resident's yes, and happens a
# minute later - "отмени выключение" cancels it meanwhile.

POWER_DELAY_SECONDS = 60


def _run(args: list[str]) -> int:
    return subprocess.run(args, capture_output=True, timeout=15, creationflags=0x08000000).returncode


async def power(tool_input: dict, ctx: TurnContext, *, run=_run) -> dict:
    action = tool_input.get("action")
    if action == "cancel":
        code = await asyncio.to_thread(run, ["shutdown", "/a"])
        return {"cancelled": True} if code == 0 else {"error": "Nothing was scheduled to shut down."}
    if action not in ("shutdown", "restart"):
        return {"error": f"Unknown action {action!r}."}
    if not tool_input.get("confirmed"):
        return {"error": f"{action} closes everything - ask the resident, then retry with confirmed=true."}
    flag = "/s" if action == "shutdown" else "/r"
    note = "Jarvis: выключение через минуту" if action == "shutdown" else "Jarvis: перезагрузка через минуту"
    code = await asyncio.to_thread(run, ["shutdown", flag, "/t", str(POWER_DELAY_SECONDS), "/c", note])
    if code != 0:
        return {"error": f"Windows refused ({code}) - maybe one is already scheduled."}
    return {"scheduled": action, "in_seconds": POWER_DELAY_SECONDS, "cancel": "отмени выключение"}


def register(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            name="media",
            description=(
                "Media keys for any player (play_pause, next, previous, stop) and the computer's volume: "
                "volume_get, volume_set (level 0-100), volume_up / volume_down (level = step, default 10), "
                "mute, unmute."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": [*MEDIA_KEYS, "volume_get", "volume_set", "volume_up",
                                                          "volume_down", "mute", "unmute"]},
                    "level": {"type": ["integer", "null"], "minimum": 0, "maximum": 100},
                },
                "required": ["action"],
            },
            handler=media,
        )
    )
    registry.register(
        Tool(
            name="desktop",
            description=(
                "The desktop: lock (lock the computer), minimize_all ('сверни все окна'), restore_windows "
                "(bring them back), status (battery, CPU, memory, disks, uptime, volume)."
            ),
            parameters={
                "type": "object",
                "properties": {"action": {"type": "string", "enum": ["lock", "minimize_all", "restore_windows",
                                                                     "status"]}},
                "required": ["action"],
            },
            handler=desktop,
        )
    )
    registry.register(
        Tool(
            name="type_text",
            description=(
                "Type text into the active window, as if on the keyboard ('напиши в текущее окно ...'). "
                "press_enter only when told to send it ('и отправь') - in a chat Enter sends the message. "
                "Not into terminals, not into my own window."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "press_enter": {"type": ["boolean", "null"]},
                },
                "required": ["text"],
            },
            handler=type_text,
        )
    )
    registry.register(
        Tool(
            name="screen",
            description=(
                "screenshot: save the screen to Pictures/Screenshots. describe: look at the screen and answer "
                "'что у меня на экране?' (optional question) - a picture of it goes to an online model, so "
                "only when asked about the screen."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["screenshot", "describe"]},
                    "question": {"type": ["string", "null"]},
                },
                "required": ["action"],
            },
            handler=screen,
        )
    )
    registry.register(
        Tool(
            name="power",
            description=(
                "shutdown / restart the computer - always ask first, then confirmed=true; it happens a minute "
                "later. cancel: 'отмени выключение'."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["shutdown", "restart", "cancel"]},
                    "confirmed": {"type": ["boolean", "null"]},
                },
                "required": ["action"],
            },
            handler=power,
        )
    )
