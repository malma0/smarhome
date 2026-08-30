"""ToolRegistry has no idea what Home Assistant or a "room" is - proven here
with a fake tool that has nothing to do with either."""

import asyncio

import pytest

from app.tools.registry import Tool, ToolRegistry, TurnContext


def make_echo_tool() -> Tool:
    async def handler(tool_input: dict, ctx: TurnContext) -> dict:
        ctx.touched.add(tool_input["target"])
        return {"echoed": tool_input["target"]}

    return Tool(
        name="echo",
        description="Echoes back whatever target it was given.",
        parameters={"type": "object", "properties": {"target": {"type": "string"}}},
        handler=handler,
    )


def test_definitions_reflect_registered_tools():
    registry = ToolRegistry()
    registry.register(make_echo_tool())

    defs = registry.definitions()

    assert len(defs) == 1
    assert defs[0].name == "echo"
    assert defs[0].parameters == {"type": "object", "properties": {"target": {"type": "string"}}}


def test_dispatch_calls_the_matching_handler():
    registry = ToolRegistry()
    registry.register(make_echo_tool())
    ctx = TurnContext()

    result = asyncio.run(registry.dispatch("echo", {"target": "kitchen"}, ctx))

    assert result == {"echoed": "kitchen"}
    assert ctx.touched == {"kitchen"}


def test_dispatch_unknown_tool_returns_error_without_raising():
    registry = ToolRegistry()
    ctx = TurnContext()

    result = asyncio.run(registry.dispatch("does_not_exist", {}, ctx))

    assert "error" in result


def test_registering_duplicate_name_raises():
    registry = ToolRegistry()
    registry.register(make_echo_tool())

    with pytest.raises(ValueError):
        registry.register(make_echo_tool())


def test_turn_context_is_shared_across_dispatches_in_one_turn():
    registry = ToolRegistry()
    registry.register(make_echo_tool())
    ctx = TurnContext()

    asyncio.run(registry.dispatch("echo", {"target": "kitchen"}, ctx))
    asyncio.run(registry.dispatch("echo", {"target": "bedroom"}, ctx))

    assert ctx.touched == {"kitchen", "bedroom"}
