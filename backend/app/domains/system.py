"""The computer itself, beyond opening apps (app.domains.computer): media
keys, the volume, the desktop (lock, minimize everything, bring it back)
and how the machine is doing (battery, CPU, memory, disks).

Windows only, no extra installs: media keys are key presses (keybd_event),
the volume is the Core Audio endpoint behind the tray's speaker icon
(IAudioEndpointVolume, through comtypes - already here for the offline
voice), the rest is plain Win32 through ctypes and pywin32. COM calls run
on a worker thread with COM initialized there, never on the voice loop.
"""

import asyncio
import ctypes
import shutil
import time
from ctypes import POINTER, byref, c_float, c_void_p, cast
from ctypes.wintypes import BOOL, DWORD, UINT

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
