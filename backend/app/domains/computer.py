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

import json
import os
import subprocess

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
