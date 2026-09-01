"""Phase 2 domain (docs/TZ.md): file operations on the computer. The user
explicitly chose NOT to sandbox this to one folder - real filesystem access
anywhere - so the one hard guard that stays regardless of confirmation is
refusing to touch a short list of OS-critical directories. This mirrors the
climate domain's absolute 5-35C ceiling that even confirmed=true can't
override: a deliberate defense-in-depth backstop, not a contradiction of
"anywhere on the computer".

Destructive actions (overwriting an existing file, deleting a file) require
confirmed=true, same pattern used throughout the project. Touching a second
distinct path in the same turn without confirming also requires it, via the
same TurnContext.touched mechanism the other domains use.
"""

import os
from pathlib import Path

from app.tools.registry import Tool, ToolRegistry, TurnContext

MAX_READ_CHARS = 20_000

_DANGEROUS_ROOTS = [
    Path(os.environ.get("SystemRoot", r"C:\Windows")),
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
    Path(os.environ.get("ProgramData", r"C:\ProgramData")),
]


def _resolve(path_str: str) -> Path | None:
    try:
        return Path(path_str).expanduser().resolve()
    except OSError:
        return None


def _is_dangerous(path: Path) -> bool:
    if path.parent == path:  # a drive root, e.g. C:\
        return True
    return any(path == root or root in path.parents for root in _DANGEROUS_ROOTS)


def _check_multi_target(path_key: str, confirmed: bool, ctx: TurnContext) -> str | None:
    others = ctx.touched - {path_key}
    if others and not confirmed:
        return (
            f"This turn already touched {sorted(others)}. Touching {path_key} too "
            "would affect multiple files at once - ask the user to confirm first, "
            "then retry with confirmed=true."
        )
    return None


async def _list_directory(tool_input: dict, ctx: TurnContext) -> dict:
    path = _resolve(tool_input.get("path") or ".")
    if path is None:
        return {"error": "Invalid path."}
    if not path.exists():
        return {"error": f"'{path}' does not exist."}
    if not path.is_dir():
        return {"error": f"'{path}' is not a directory."}
    try:
        entries = [
            {
                "name": child.name,
                "type": "dir" if child.is_dir() else "file",
                "size": child.stat().st_size if child.is_file() else None,
            }
            for child in sorted(path.iterdir())
        ]
    except OSError as exc:
        return {"error": str(exc)}
    return {"path": str(path), "entries": entries}


async def _read_file(tool_input: dict, ctx: TurnContext) -> dict:
    path = _resolve(tool_input["path"])
    if path is None:
        return {"error": "Invalid path."}
    if not path.exists():
        return {"error": f"'{path}' does not exist."}
    if not path.is_file():
        return {"error": f"'{path}' is not a file."}
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        return {"error": f"'{path}' does not look like a text file (binary content)."}
    except OSError as exc:
        return {"error": str(exc)}
    truncated = len(text) > MAX_READ_CHARS
    return {"path": str(path), "content": text[:MAX_READ_CHARS], "truncated": truncated}


async def _write_file(tool_input: dict, ctx: TurnContext) -> dict:
    path = _resolve(tool_input["path"])
    confirmed = bool(tool_input.get("confirmed", False))
    if path is None:
        return {"error": "Invalid path."}
    if _is_dangerous(path):
        return {"error": f"Refusing to write to '{path}' - protected system location, no exceptions."}
    if not path.parent.exists():
        return {"error": f"Parent directory '{path.parent}' does not exist - create it first."}
    if path.exists() and not confirmed:
        return {
            "error": f"'{path}' already exists. Ask the user to confirm overwriting it, "
            "then retry with confirmed=true."
        }
    if err := _check_multi_target(str(path), confirmed, ctx):
        return {"error": err}
    try:
        path.write_text(tool_input.get("content", ""), encoding="utf-8")
    except OSError as exc:
        return {"error": str(exc)}
    ctx.touched.add(str(path))
    return {"ok": True, "path": str(path)}


async def _delete_file(tool_input: dict, ctx: TurnContext) -> dict:
    path = _resolve(tool_input["path"])
    confirmed = bool(tool_input.get("confirmed", False))
    if path is None:
        return {"error": "Invalid path."}
    if _is_dangerous(path):
        return {"error": f"Refusing to delete '{path}' - protected system location, no exceptions."}
    if not path.exists():
        return {"error": f"'{path}' does not exist."}
    if not path.is_file():
        return {"error": f"'{path}' is not a file (refusing to recursively delete directories)."}
    if not confirmed:
        return {
            "error": "Deleting a file is irreversible - ask the user to explicitly confirm, "
            "then retry with confirmed=true."
        }
    if err := _check_multi_target(str(path), confirmed, ctx):
        return {"error": err}
    try:
        path.unlink()
    except OSError as exc:
        return {"error": str(exc)}
    ctx.touched.add(str(path))
    return {"ok": True, "deleted": str(path)}


def register(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            name="list_directory",
            description="List files and subdirectories at a path.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path. Defaults to the current directory.",
                    }
                },
            },
            handler=_list_directory,
        )
    )
    registry.register(
        Tool(
            name="read_file",
            description=f"Read a text file's contents (up to {MAX_READ_CHARS} characters).",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            handler=_read_file,
        )
    )
    registry.register(
        Tool(
            name="write_file",
            description=(
                "Create or overwrite a text file. Overwriting an existing file, or "
                "writing a second file in the same turn, requires the user's explicit "
                "confirmation (confirmed=true)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "confirmed": {"type": "boolean", "default": False},
                },
                "required": ["path", "content"],
            },
            handler=_write_file,
        )
    )
    registry.register(
        Tool(
            name="delete_file",
            description=(
                "Delete a file. Always requires the user's explicit confirmation "
                "(confirmed=true) - this is irreversible."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "confirmed": {"type": "boolean", "default": False},
                },
                "required": ["path"],
            },
            handler=_delete_file,
        )
    )
