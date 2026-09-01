import asyncio

import pytest

import app.domains.files as files_module
from app.domains.files import register
from app.tools.registry import ToolRegistry, TurnContext


def make_registry() -> ToolRegistry:
    registry = ToolRegistry()
    register(registry)
    return registry


def dispatch(registry, name, tool_input, ctx=None):
    return asyncio.run(registry.dispatch(name, tool_input, ctx or TurnContext()))


def test_registers_four_tools():
    registry = make_registry()
    names = {t.name for t in registry.definitions()}
    assert names == {"list_directory", "read_file", "write_file", "delete_file"}


# --- list_directory ---


def test_list_directory_lists_files_and_dirs(tmp_path):
    (tmp_path / "a.txt").write_text("hi")
    (tmp_path / "sub").mkdir()
    registry = make_registry()

    result = dispatch(registry, "list_directory", {"path": str(tmp_path)})

    names_and_types = {(e["name"], e["type"]) for e in result["entries"]}
    assert names_and_types == {("a.txt", "file"), ("sub", "dir")}


def test_list_directory_missing_path_errors(tmp_path):
    registry = make_registry()
    result = dispatch(registry, "list_directory", {"path": str(tmp_path / "nope")})
    assert "error" in result


def test_list_directory_on_a_file_errors(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hi")
    registry = make_registry()
    result = dispatch(registry, "list_directory", {"path": str(f)})
    assert "error" in result


# --- read_file ---


def test_read_file_returns_content(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello world")
    registry = make_registry()

    result = dispatch(registry, "read_file", {"path": str(f)})

    assert result == {"path": str(f.resolve()), "content": "hello world", "truncated": False}


def test_read_file_truncates_long_content(tmp_path, monkeypatch):
    monkeypatch.setattr(files_module, "MAX_READ_CHARS", 5)
    f = tmp_path / "a.txt"
    f.write_text("0123456789")
    registry = make_registry()

    result = dispatch(registry, "read_file", {"path": str(f)})

    assert result["content"] == "01234"
    assert result["truncated"] is True


def test_read_file_missing_errors(tmp_path):
    registry = make_registry()
    result = dispatch(registry, "read_file", {"path": str(tmp_path / "nope.txt")})
    assert "error" in result


def test_read_file_binary_content_errors_gracefully(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(bytes([0xFF, 0xFE, 0x00, 0xD8, 0x00]))
    registry = make_registry()

    result = dispatch(registry, "read_file", {"path": str(f)})

    assert "error" in result


# --- write_file ---


def test_write_file_creates_new_file_without_confirmation(tmp_path):
    target = tmp_path / "new.txt"
    registry = make_registry()

    result = dispatch(registry, "write_file", {"path": str(target), "content": "hi"})

    assert result["ok"] is True
    assert target.read_text() == "hi"


def test_write_file_existing_without_confirmation_is_rejected(tmp_path):
    target = tmp_path / "existing.txt"
    target.write_text("original")
    registry = make_registry()

    result = dispatch(registry, "write_file", {"path": str(target), "content": "new"})

    assert "error" in result
    assert target.read_text() == "original"


def test_write_file_existing_with_confirmation_overwrites(tmp_path):
    target = tmp_path / "existing.txt"
    target.write_text("original")
    registry = make_registry()

    result = dispatch(
        registry, "write_file", {"path": str(target), "content": "new", "confirmed": True}
    )

    assert result["ok"] is True
    assert target.read_text() == "new"


def test_write_file_to_dangerous_root_rejected_even_confirmed(tmp_path, monkeypatch):
    fake_windows = tmp_path / "FakeWindows"
    fake_windows.mkdir()
    monkeypatch.setattr(files_module, "_DANGEROUS_ROOTS", [fake_windows.resolve()])
    target = fake_windows / "evil.txt"
    registry = make_registry()

    result = dispatch(
        registry, "write_file", {"path": str(target), "content": "x", "confirmed": True}
    )

    assert "error" in result
    assert not target.exists()


def test_write_file_missing_parent_directory_errors(tmp_path):
    target = tmp_path / "missing_dir" / "file.txt"
    registry = make_registry()

    result = dispatch(registry, "write_file", {"path": str(target), "content": "x"})

    assert "error" in result


# --- delete_file ---


def test_delete_file_without_confirmation_is_rejected(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("keep me")
    registry = make_registry()

    result = dispatch(registry, "delete_file", {"path": str(target)})

    assert "error" in result
    assert target.exists()


def test_delete_file_with_confirmation_removes_it(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("bye")
    registry = make_registry()

    result = dispatch(registry, "delete_file", {"path": str(target), "confirmed": True})

    assert result["ok"] is True
    assert not target.exists()


def test_delete_directory_is_refused(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    registry = make_registry()

    result = dispatch(registry, "delete_file", {"path": str(sub), "confirmed": True})

    assert "error" in result
    assert sub.exists()


def test_delete_missing_file_errors(tmp_path):
    registry = make_registry()
    result = dispatch(registry, "delete_file", {"path": str(tmp_path / "nope.txt"), "confirmed": True})
    assert "error" in result


def test_delete_file_in_dangerous_root_rejected_even_confirmed(tmp_path, monkeypatch):
    fake_windows = tmp_path / "FakeWindows"
    fake_windows.mkdir()
    target = fake_windows / "important.dll"
    target.write_text("critical")
    monkeypatch.setattr(files_module, "_DANGEROUS_ROOTS", [fake_windows.resolve()])
    registry = make_registry()

    result = dispatch(registry, "delete_file", {"path": str(target), "confirmed": True})

    assert "error" in result
    assert target.exists()


# --- multi-target guard across one turn ---


def test_second_file_write_in_same_turn_requires_confirmation(tmp_path):
    registry = make_registry()
    ctx = TurnContext()
    dispatch(registry, "write_file", {"path": str(tmp_path / "one.txt"), "content": "a"}, ctx)

    result = dispatch(registry, "write_file", {"path": str(tmp_path / "two.txt"), "content": "b"}, ctx)

    assert "error" in result
    assert not (tmp_path / "two.txt").exists()


def test_second_file_write_in_same_turn_allowed_with_confirmation(tmp_path):
    registry = make_registry()
    ctx = TurnContext()
    dispatch(registry, "write_file", {"path": str(tmp_path / "one.txt"), "content": "a"}, ctx)

    result = dispatch(
        registry,
        "write_file",
        {"path": str(tmp_path / "two.txt"), "content": "b", "confirmed": True},
        ctx,
    )

    assert result["ok"] is True
    assert (tmp_path / "two.txt").read_text() == "b"


def test_read_only_operations_do_not_count_toward_multi_target_guard(tmp_path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    registry = make_registry()
    ctx = TurnContext()

    dispatch(registry, "read_file", {"path": str(tmp_path / "a.txt")}, ctx)
    result = dispatch(registry, "read_file", {"path": str(tmp_path / "b.txt")}, ctx)

    assert "content" in result
