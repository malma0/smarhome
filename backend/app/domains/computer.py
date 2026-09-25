"""Phase 2 domain (docs/TZ.md): basic control over the computer itself,
registered into the generic ToolRegistry built in phase 0. This exists as a
tool-use proving ground before any Home Assistant domain - real, visible
consequences (a program actually opens) without needing physical hardware.

Two ways an app gets opened:
1. A small fixed allowlist (KNOWN_APPS) of core Windows utilities, launched
   directly by exe name - fast, no external calls, always available.
2. Anything else is looked up among every app Windows itself knows how to
   launch (Get-StartApps - the same list Start Menu search uses), matched
   by name, and launched via the universal `explorer.exe shell:AppsFolder\\
   <AppID>` mechanism. This is how "open Steam" (or literally any other
   installed application) works without hardcoding a path for each one.

Deliberately excluded from BOTH paths: shell/terminal apps (cmd, powershell,
wsl, bash, terminal, and their variants - Get-StartApps lists these too,
e.g. "Developer PowerShell for VS 2022", "Git Bash", "WSL"). Opening a
program is meant to stay a low-risk, reversible action. Giving a voice
command a path to a shell prompt is a fundamentally bigger risk than "open
notepad" or even "open Steam", and isn't a decision to make implicitly by
letting the generic lookup find one by accident - it's excluded on purpose
(see _is_blocked_app_name), and stays excluded unless that's revisited
deliberately later.
"""

import asyncio
import ctypes
import json
import os
import subprocess
import time

from app.config import settings
from app.tools.registry import Tool, ToolRegistry, TurnContext

# Fast path: core Windows utilities, launched directly, no external calls.
KNOWN_APPS: dict[str, str] = {
    "notepad": "notepad.exe",
    "блокнот": "notepad.exe",
    "calculator": "calc.exe",
    "калькулятор": "calc.exe",
    "paint": "mspaint.exe",
    "paint 3d": "mspaint.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "проводник": "explorer.exe",
}

# The default browser has no fixed executable name across machines - opening
# a URL via os.startfile hands it to whatever's registered as default.
BROWSER_ALIASES = {"browser", "web browser", "браузер", "интернет"}

# Checked against both the requested name and whatever Get-StartApps matched
# it to - substring match on purpose, so "Developer PowerShell for VS 2022"
# and "Git Bash" are caught, not just exact "cmd"/"powershell".
_BLOCKED_APP_KEYWORDS = (
    "powershell",
    "cmd",
    "command prompt",
    "terminal",
    "wsl",
    "bash",
    "cmder",
    "shell",
)

TOOL_DESCRIPTION = (
    "Open an app by name: notepad, calculator, paint, explorer, the default browser, or any installed "
    "app ('Steam', 'Discord'). Never a shell or terminal. Nothing installed by that name - say so, don't "
    "substitute. browser + url opens a site ('открой YouTube'). To show text in notepad: write_file "
    "first, then pass that path as file (built-in utilities only)."
)


def _is_blocked_app_name(name: str) -> bool:
    lowered = name.lower()
    return any(keyword in lowered for keyword in _BLOCKED_APP_KEYWORDS)


def _list_start_apps() -> list[dict]:
    """Everything Windows itself can launch from the Start Menu - same data
    Start Menu search uses. Returns [] on any failure rather than raising;
    a missing/broken PowerShell shouldn't take down the whole tool."""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", "Get-StartApps | ConvertTo-Json -Compress"],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
    except (subprocess.SubprocessError, OSError):
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    return [app for app in data if isinstance(app, dict) and app.get("Name") and app.get("AppID")]


def _find_installed_app(query: str) -> dict | None:
    """Exact name match first, then substring, so 'steam' finds 'Steam' and
    'blender' finds 'Blender' without needing the exact display name."""
    query_lower = query.strip().lower()
    apps = _list_start_apps()
    for app in apps:
        if app["Name"].strip().lower() == query_lower:
            return app
    for app in apps:
        if query_lower in app["Name"].strip().lower():
            return app
    return None


# Only used if the real default browser can't be found via the registry -
# should be rare. Not the normal case, so it doesn't need to be anyone's
# actual homepage.
_FALLBACK_BROWSER_URL = "https://www.google.com"


def _default_browser_executable() -> str | None:
    """Looks up the user's actual default browser via the registry, so
    'open the browser' with no site launches it plainly - showing its own
    configured home/new-tab page - instead of os.startfile-ing a URL we
    picked (previously always google.com, which isn't anyone's homepage)."""
    import shlex
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice",
        ) as key:
            prog_id, _ = winreg.QueryValueEx(key, "ProgId")
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{prog_id}\shell\open\command") as key:
            command, _ = winreg.QueryValueEx(key, "")
    except OSError:
        return None

    try:
        parts = shlex.split(command, posix=False)
    except ValueError:
        return None
    return parts[0].strip('"') if parts else None


def _normalize_url(raw_url: str) -> str | None:
    """Only http/https are allowed - other schemes (file:, mailto:, custom
    protocol handlers) can do more than 'open a website' implies, so they're
    rejected rather than silently forwarded to os.startfile. A bare site
    name with no scheme at all (e.g. 'youtube.com') is assumed https."""
    url = raw_url.strip()
    if not url:
        return None
    if "://" in url:
        scheme = url.split("://", 1)[0].lower()
        return url if scheme in ("http", "https") else None
    return f"https://{url}"


async def _open_application(tool_input: dict, ctx: TurnContext) -> dict:
    app = tool_input["app"].strip().lower()

    if app in BROWSER_ALIASES:
        raw_url = tool_input.get("url") or ""
        if raw_url:
            url = _normalize_url(raw_url)
            if url is None:
                return {"error": f"'{raw_url}' is not a usable http/https address."}
            os.startfile(url)
            ctx.touched.add("browser")
            return {"ok": True, "opened": "browser", "url": url}

        browser_exe = _default_browser_executable()
        if browser_exe and os.path.exists(browser_exe):
            subprocess.Popen([browser_exe])
        else:
            os.startfile(_FALLBACK_BROWSER_URL)
        ctx.touched.add("browser")
        return {"ok": True, "opened": "browser"}

    if _is_blocked_app_name(app):
        return {"error": f"'{tool_input['app']}' is not allowed - no shell/terminal access, no exceptions."}

    exe = KNOWN_APPS.get(app)
    if exe:
        file_path = tool_input.get("file") or ""
        if file_path:
            if not os.path.exists(file_path):
                return {"error": f"File not found: {file_path}"}
            subprocess.Popen([exe, file_path])
        else:
            subprocess.Popen([exe])
        ctx.touched.add(app)
        return {"ok": True, "opened": app}

    match = _find_installed_app(tool_input["app"])
    if match is None:
        return {"error": f"No installed application matching '{tool_input['app']}' was found."}
    if _is_blocked_app_name(match["Name"]):
        return {"error": f"'{match['Name']}' is not allowed - no shell/terminal access, no exceptions."}

    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{match['AppID']}"])
    ctx.touched.add(match["Name"])
    return {"ok": True, "opened": match["Name"]}


# --------------------------------------------------------------- closing apps
#
# "Закрой Steam": the app's windows get WM_CLOSE - the same as clicking the
# cross, so an app with unsaved work asks about it itself. Many apps (Steam,
# Discord) only hide into the tray on that; then the process is still
# running, and quitting it for good - killing it - takes the resident's yes,
# as does anything that didn't close on its own.

WM_CLOSE = 0x0010
CLOSE_WAIT_SECONDS = 3
# The desktop and taskbar are explorer.exe windows too - never "closed".
_SHELL_WINDOW_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}
# Windows' own processes - closing them breaks the session.
_SYSTEM_EXES = {
    "csrss.exe", "winlogon.exe", "dwm.exe", "lsass.exe", "services.exe", "svchost.exe", "sihost.exe",
    "smss.exe", "wininit.exe", "fontdrvhost.exe", "ctfmon.exe", "searchhost.exe", "textinputhost.exe",
    "startmenuexperiencehost.exe", "shellexperiencehost.exe", "lockapp.exe", "system",
}
# These run the house (Home Assistant, the danger alarms) - a yes first.
_CONFIRM_EXES = {"docker desktop.exe": "Docker runs Home Assistant - the house and the danger alarms stop with it"}


def _exe_of(pid: int) -> str:
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_ulong(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        kernel32.CloseHandle(handle)


def list_windows() -> list[dict]:
    """Visible top-level app windows: hwnd, pid, exe, title."""
    import win32gui
    import win32process

    found = []

    def visit(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd) or win32gui.GetWindow(hwnd, 4):  # GW_OWNER: dialogs, popups
            return
        title = win32gui.GetWindowText(hwnd)
        if not title or win32gui.GetClassName(hwnd) in _SHELL_WINDOW_CLASSES:
            return
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        found.append({"hwnd": hwnd, "pid": pid, "exe": _exe_of(pid), "title": title})

    win32gui.EnumWindows(visit, None)
    return found


def list_processes() -> list[dict]:
    import win32process

    return [{"pid": pid, "exe": exe} for pid in win32process.EnumProcesses() if pid and (exe := _exe_of(pid))]


def _post_close(hwnd: int) -> None:
    import win32gui

    win32gui.PostMessage(hwnd, WM_CLOSE, 0, 0)


def _window_open(hwnd: int) -> bool:
    import win32gui

    return bool(win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd))


def _terminate(pid: int) -> None:
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
    if handle:
        kernel32.TerminateProcess(handle, 1)
        kernel32.CloseHandle(handle)


def _own_pids() -> set[int]:
    """Jarvis itself: this process and its launcher (a venv's pythonw starts
    the real interpreter as its child)."""
    return {os.getpid(), os.getppid()}


def _wanted_exes(name: str) -> set[str]:
    """'блокнот' -> {'notepad.exe'}, 'браузер' -> the default browser's exe."""
    exes = set()
    if name in KNOWN_APPS:
        exes.add(KNOWN_APPS[name].lower())
    if name in BROWSER_ALIASES and (browser := _default_browser_executable()):
        exes.add(os.path.basename(browser).lower())
    return exes


def _matches(item: dict, name: str, exes: set[str], by_title: bool) -> bool:
    exe = item["exe"].lower()
    stem = exe.removesuffix(".exe")
    if exe in exes or stem == name or (len(name) >= 3 and name in stem):
        return True
    return by_title and len(name) >= 3 and name in item.get("title", "").casefold()


class AppCloser:
    """The OS side is injectable - tests don't close real windows."""

    def __init__(self, windows=list_windows, processes=list_processes, post_close=_post_close,
                 window_open=_window_open, terminate=_terminate, own_pids=_own_pids, sleep=time.sleep):
        self.windows, self.processes = windows, processes
        self.post_close, self.window_open, self.terminate = post_close, window_open, terminate
        self.own_pids, self.sleep = own_pids, sleep

    def close(self, app: str, confirmed: bool) -> dict:
        name = app.strip().casefold()
        if name in ("jarvis", "джарвис", "джервис"):
            return {"error": "That's me - I don't close myself."}
        exes = _wanted_exes(name)
        own = self.own_pids()
        windows = [w for w in self.windows() if w["pid"] not in own]
        by_exe = [w for w in windows if _matches(w, name, exes, by_title=False)]
        targets = by_exe or [w for w in windows if _matches(w, name, exes, by_title=True)]
        pids = {w["pid"] for w in targets}
        exe_names = {w["exe"].lower() for w in targets}
        if not targets:  # maybe it only lives in the tray
            running = [p for p in self.processes() if p["pid"] not in own and _matches(p, name, exes, False)]
            pids, exe_names = {p["pid"] for p in running}, {p["exe"].lower() for p in running}
        if not pids:
            return {"error": f"Nothing called '{app}' is running."}
        if exe_names & _SYSTEM_EXES:
            return {"error": f"'{app}' is part of Windows itself - not closed."}
        for exe in exe_names & set(_CONFIRM_EXES):
            if not confirmed:
                return {"error": f"{_CONFIRM_EXES[exe]} - ask the resident, then retry with confirmed=true."}

        if targets:
            for window in targets:
                self.post_close(window["hwnd"])
            waited = 0.0
            while waited < CLOSE_WAIT_SECONDS and any(self.window_open(w["hwnd"]) for w in targets):
                self.sleep(0.2)
                waited += 0.2
        still_open = [w["title"] for w in targets if self.window_open(w["hwnd"])]
        alive = {p["pid"] for p in self.processes() if p["pid"] in pids}
        waited = 0.0
        while alive and not still_open and targets and waited < CLOSE_WAIT_SECONDS:
            # Seen live: Notepad's process outlives its window by a moment -
            # that's not "hid in the tray".
            self.sleep(0.2)
            waited += 0.2
            alive = {p["pid"] for p in self.processes() if p["pid"] in pids}
        label = sorted(exe_names)[0].removesuffix(".exe")

        if not still_open and not alive:
            return {"closed": label}
        if not confirmed:
            if still_open:
                return {"error": (f"'{label}' didn't close - it may be asking to save something. Ask the resident; "
                                  "on yes retry with confirmed=true to force it (unsaved work is lost)."),
                        "still_open": still_open}
            return {"closed_windows": bool(targets), "still_running": label,
                    "note": ("Its window is gone but it keeps running in the background (tray). To quit it "
                             "completely, ask the resident and retry with confirmed=true.")}
        for pid in alive:
            self.terminate(pid)
        return {"closed": label, "forced": True}


_closer = AppCloser()


async def _close_application(tool_input: dict, ctx: TurnContext) -> dict:
    app = (tool_input.get("app") or "").strip()
    if not app:
        return {"error": "Which application?"}
    return await asyncio.to_thread(_closer.close, app, bool(tool_input.get("confirmed")))


# --------------------------------------------------------------- searching

SEARCH_SITES = {
    "youtube": "https://www.youtube.com/results?search_query={query}",
    "wikipedia": "https://ru.wikipedia.org/w/index.php?search={query}",
    "maps": "https://yandex.ru/maps/?text={query}",
}


def search_url(query: str, site: str = "web") -> str:
    from urllib.parse import quote_plus

    template = SEARCH_SITES.get(site) or settings.web_search_url
    return template.replace("{query}", quote_plus(query))


async def _search_web(tool_input: dict, ctx: TurnContext) -> dict:
    query = (tool_input.get("query") or "").strip()
    if not query:
        return {"error": "Nothing to search for."}
    url = search_url(query, tool_input.get("site") or "web")
    os.startfile(url)  # the default browser, a new tab
    return {"searched": query, "site": tool_input.get("site") or "web"}


def register(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            name="open_application",
            description=TOOL_DESCRIPTION,
            parameters={
                "type": "object",
                "properties": {
                    "app": {
                        "type": "string",
                        "description": "Application name, e.g. 'notepad', 'calculator', 'browser'.",
                    },
                    "url": {
                        "type": "string",
                        "description": (
                            "Only used when app is 'browser'. A specific site to open, e.g. "
                            "'youtube.com' or 'https://www.youtube.com'. Omit to open a default page."
                        ),
                    },
                    "file": {
                        "type": "string",
                        "description": (
                            "Only works for built-in utilities (notepad, paint, etc.), not other "
                            "installed applications. Path to a file to open directly in the app "
                            "(e.g. after writing to it with write_file), so it opens already "
                            "showing that content instead of blank."
                        ),
                    },
                },
                "required": ["app"],
            },
            handler=_open_application,
        )
    )
    registry.register(
        Tool(
            name="close_application",
            description=(
                "Close an app by name ('закрой Steam', 'закрой блокнот') the way its close button does - it "
                "asks about unsaved work itself. If it didn't close, or only hid into the tray, the result "
                "says so: ask the resident, and on yes retry with confirmed=true to force it."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "app": {"type": "string"},
                    "confirmed": {"type": ["boolean", "null"], "default": False},
                },
                "required": ["app"],
            },
            handler=_close_application,
        )
    )
    registry.register(
        Tool(
            name="search_web",
            description=(
                "Search in the browser (a new tab): site 'web' (default), 'youtube', 'wikipedia' or 'maps'. "
                "'найди в интернете рецепт борща', 'найди на ютубе котиков'."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "site": {"type": ["string", "null"], "enum": ["web", "youtube", "wikipedia", "maps", None]},
                },
                "required": ["query"],
            },
            handler=_search_web,
        )
    )
