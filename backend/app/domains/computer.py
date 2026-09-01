"""Phase 2 domain (docs/TZ.md): basic control over the computer itself,
registered into the generic ToolRegistry built in phase 0. This exists as a
tool-use proving ground before any Home Assistant domain - real, visible
consequences (a program actually opens) without needing physical hardware.

Deliberately excluded from the allowlist: shell/terminal apps (cmd,
powershell, wsl, bash, terminal) - opening a program is meant to stay a
low-risk, reversible action. Giving a voice command a path to a shell
prompt is a fundamentally bigger risk than "open notepad", and isn't a
decision to make implicitly by leaving it off an allowlist by accident -
it's excluded on purpose, and stays excluded unless that's revisited
deliberately later.
"""

import os
import subprocess

from app.tools.registry import Tool, ToolRegistry, TurnContext

# Explicit allowlist only - no arbitrary executable names, no shell=True.
# Windows built-ins resolve via PATH (System32 is always on it).
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

TOOL_DESCRIPTION = (
    "Open a known desktop application by name (notepad, calculator, paint, "
    "file explorer, or the default web browser). Only applications on a "
    "fixed allowlist can be opened - there is no shell/terminal access and "
    "no way to run arbitrary commands. If asked for something not "
    "supported, say so rather than guessing at a substitute. When app is "
    "'browser' and the user named a site (e.g. 'open YouTube'), pass its "
    "address as url - otherwise the browser opens to a generic default "
    "page, which is not what was asked for."
)

DEFAULT_BROWSER_URL = "https://www.google.com"


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
        url = _normalize_url(raw_url) if raw_url else DEFAULT_BROWSER_URL
        if url is None:
            return {"error": f"'{raw_url}' is not a usable http/https address."}
        os.startfile(url)
        ctx.touched.add("browser")
        return {"ok": True, "opened": "browser", "url": url}

    exe = KNOWN_APPS.get(app)
    if not exe:
        return {
            "error": (
                f"'{tool_input['app']}' is not on the allowed application list. "
                f"Known: {sorted(set(KNOWN_APPS) | BROWSER_ALIASES)}."
            )
        }

    subprocess.Popen([exe])
    ctx.touched.add(app)
    return {"ok": True, "opened": app}


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
                },
                "required": ["app"],
            },
            handler=_open_application,
        )
    )
