"""No real program ever launches in these tests - subprocess.Popen and
os.startfile are mocked throughout."""

import asyncio
from unittest.mock import patch

from app.domains.computer import KNOWN_APPS, register
from app.tools.registry import ToolRegistry, TurnContext


def make_registry() -> ToolRegistry:
    registry = ToolRegistry()
    register(registry)
    return registry


def test_registers_exactly_one_tool():
    registry = make_registry()
    names = [t.name for t in registry.definitions()]
    assert names == ["open_application"]


def test_opens_known_app_by_exact_name():
    registry = make_registry()
    ctx = TurnContext()
    with patch("app.domains.computer.subprocess.Popen") as mock_popen:
        result = asyncio.run(registry.dispatch("open_application", {"app": "notepad"}, ctx))

    mock_popen.assert_called_once_with(["notepad.exe"])
    assert result == {"ok": True, "opened": "notepad"}
    assert "notepad" in ctx.touched


def test_matching_is_case_insensitive():
    registry = make_registry()
    ctx = TurnContext()
    with patch("app.domains.computer.subprocess.Popen") as mock_popen:
        asyncio.run(registry.dispatch("open_application", {"app": "Calculator"}, ctx))

    mock_popen.assert_called_once_with(["calc.exe"])


def test_russian_alias_resolves_to_same_executable():
    registry = make_registry()
    ctx = TurnContext()
    with patch("app.domains.computer.subprocess.Popen") as mock_popen:
        asyncio.run(registry.dispatch("open_application", {"app": "блокнот"}, ctx))

    mock_popen.assert_called_once_with(["notepad.exe"])


def test_browser_alias_with_no_url_opens_default_page():
    registry = make_registry()
    ctx = TurnContext()
    with (
        patch("app.domains.computer.os.startfile") as mock_startfile,
        patch("app.domains.computer.subprocess.Popen") as mock_popen,
    ):
        result = asyncio.run(registry.dispatch("open_application", {"app": "browser"}, ctx))

    mock_startfile.assert_called_once_with("https://www.google.com")
    mock_popen.assert_not_called()
    assert result == {"ok": True, "opened": "browser", "url": "https://www.google.com"}


def test_browser_with_bare_site_name_gets_https_prefix():
    registry = make_registry()
    ctx = TurnContext()
    with patch("app.domains.computer.os.startfile") as mock_startfile:
        result = asyncio.run(
            registry.dispatch("open_application", {"app": "browser", "url": "youtube.com"}, ctx)
        )

    mock_startfile.assert_called_once_with("https://youtube.com")
    assert result == {"ok": True, "opened": "browser", "url": "https://youtube.com"}


def test_browser_with_full_url_passed_through_unchanged():
    registry = make_registry()
    ctx = TurnContext()
    with patch("app.domains.computer.os.startfile") as mock_startfile:
        asyncio.run(
            registry.dispatch(
                "open_application", {"app": "browser", "url": "http://example.com/page"}, ctx
            )
        )

    mock_startfile.assert_called_once_with("http://example.com/page")


def test_russian_browser_alias_supports_url_too():
    registry = make_registry()
    ctx = TurnContext()
    with patch("app.domains.computer.os.startfile") as mock_startfile:
        asyncio.run(
            registry.dispatch("open_application", {"app": "браузер", "url": "youtube.com"}, ctx)
        )

    mock_startfile.assert_called_once_with("https://youtube.com")


def test_non_http_scheme_url_is_rejected():
    registry = make_registry()
    ctx = TurnContext()
    with patch("app.domains.computer.os.startfile") as mock_startfile:
        result = asyncio.run(
            registry.dispatch("open_application", {"app": "browser", "url": "file:///C:/secrets.txt"}, ctx)
        )

    mock_startfile.assert_not_called()
    assert "error" in result


def test_unknown_app_rejected_without_launching_anything():
    registry = make_registry()
    ctx = TurnContext()
    with (
        patch("app.domains.computer.subprocess.Popen") as mock_popen,
        patch("app.domains.computer.os.startfile") as mock_startfile,
    ):
        result = asyncio.run(registry.dispatch("open_application", {"app": "cmd"}, ctx))

    assert "error" in result
    mock_popen.assert_not_called()
    mock_startfile.assert_not_called()
    assert ctx.touched == set()


def test_shell_and_terminal_apps_are_never_in_the_allowlist():
    for shell_name in ("cmd", "powershell", "bash", "wsl", "terminal", "sh", "cmd.exe", "powershell.exe"):
        assert shell_name not in KNOWN_APPS
