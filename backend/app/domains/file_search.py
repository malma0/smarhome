"""Finding files by name - "где мой отчёт за сентябрь?", "найди фото с моря
и открой".

Searched: the resident's own folders (Desktop, Documents, Downloads,
Pictures, Music, Videos, OneDrive), or a folder that's named. Names match
by word stems, so "отчёт за сентябрь" finds "Отчеты_сентябрь_2026.docx";
the newest come first. Bounded by time and count - a search is a voice
answer, not an indexing job. Opening a found file uses its own app; programs
and scripts are never started this way (running something is a different
thing from opening a document).
"""

import asyncio
import os
import time
from datetime import datetime
from pathlib import Path

from app.tools.registry import Tool, ToolRegistry, TurnContext

USER_FOLDERS = ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos", "OneDrive")
SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "__pycache__", "AppData", "$RECYCLE.BIN", "site-packages"}
TIME_BUDGET_SECONDS = 4.0
MAX_SCANNED = 200_000
MAX_RESULTS = 10
# Opening these would run something, not show a document.
NEVER_OPEN = {".exe", ".msi", ".bat", ".cmd", ".ps1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".lnk",
              ".scr", ".com", ".reg", ".hta", ".pif", ".cpl", ".jar", ".py", ".pyw", ".sh", ".url"}
_SHORT = 3  # "за", "от", "и" aren't in file names


def default_roots() -> list[Path]:
    home = Path.home()
    return [p for p in (home / name for name in USER_FOLDERS) if p.is_dir()]


def _norm(text: str) -> str:
    text = text.casefold().replace("ё", "е")
    return "".join(c if c.isalnum() else " " for c in text)


def _stems(query: str) -> list[str]:
    """'отчёт за сентябрь' -> ['отче', 'сентяб']: endings differ in Russian."""
    return [w[:max(4, len(w) - 2)] if len(w) > 4 else w for w in _norm(query).split() if len(w) >= _SHORT]


def search(query: str, roots: list[Path], budget: float = TIME_BUDGET_SECONDS) -> list[dict]:
    stems = _stems(query)
    if not stems:
        return []
    found, scanned, deadline = [], 0, time.monotonic() + budget
    stack = list(roots)
    while stack and scanned < MAX_SCANNED and time.monotonic() < deadline:
        folder = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            scanned += 1
            name = entry.name
            if name.startswith(".") or name in SKIP_DIRS:
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(entry.path)
                    continue
            except OSError:
                continue
            hits = sum(stem in _norm(name) for stem in stems)
            if hits:
                try:
                    stat = entry.stat()
                except OSError:
                    continue
                found.append((hits, stat.st_mtime, entry.path, stat.st_size))
    best = max((f[0] for f in found), default=0)
    found = [f for f in found if f[0] == best]  # only the fullest matches
    found.sort(key=lambda f: -f[1])
    return [{"path": path, "modified": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"),
             "size_kb": round(size / 1024), "matched_words": hits}
            for hits, mtime, path, size in found[:MAX_RESULTS]]


def make_handler(roots=default_roots, opener=None):
    async def find_files(tool_input: dict, ctx: TurnContext) -> dict:
        query = (tool_input.get("name") or "").strip()
        if not query:
            return {"error": "What's the file called?"}
        folder = (tool_input.get("folder") or "").strip()
        where = [Path(folder).expanduser()] if folder else roots()
        if folder and not where[0].is_dir():
            return {"error": f"No folder '{folder}'."}
        results = await asyncio.to_thread(search, query, where)
        if not results:
            return {"found": [], "searched": [str(p) for p in where]}
        answer = {"found": results}
        if tool_input.get("open"):
            path = results[0]["path"]
            if Path(path).suffix.lower() in NEVER_OPEN:
                answer["error"] = f"'{Path(path).name}' is a program or script - not started this way."
            else:
                (opener or os.startfile)(path)
                answer["opened"] = path
        return answer

    return find_files


def register(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            name="find_files",
            description=(
                "Find files by words of their name in the resident's folders (Desktop, Documents, Downloads, "
                "Pictures, Music, Videos, OneDrive) or in a given folder; newest first. open=true also opens "
                "the best match in its own app (programs and scripts are never started)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "folder": {"type": ["string", "null"]},
                    "open": {"type": ["boolean", "null"]},
                },
                "required": ["name"],
            },
            handler=make_handler(),
        )
    )
