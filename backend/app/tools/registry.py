"""Device-agnostic tool registry. JarvisAgent never knows what a "room" or a
"light" is - it just asks the registry for tool definitions to hand the LLM,
and dispatches tool calls back through it. Concrete domains (light, climate,
lock, ...) register their tools here in later phases; this module has zero
knowledge of Home Assistant or any other integration.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.llm.base import ToolDef

ToolHandler = Callable[[dict, "TurnContext"], Awaitable[dict]]


@dataclass
class TurnContext:
    """State scoped to one user turn (one JarvisAgent.chat() call), shared
    across every tool dispatched during that turn. `touched` lets a domain's
    handler implement cross-call guards (e.g. "confirm before touching a
    second room") without the registry or agent needing to know what a
    "room" is - domains just agree on what string they put in here."""

    touched: set[str] = field(default_factory=set)
    said: str = ""  # the person's words this turn - for guards that check the model against them


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict
    handler: ToolHandler


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered.")
        self._tools[tool.name] = tool

    def definitions(self, names: set[str] | None = None) -> list[ToolDef]:
        """All tools, or only those named (app.tools.router picks them)."""
        return [
            ToolDef(name=t.name, description=t.description, parameters=t.parameters)
            for t in self._tools.values()
            if names is None or t.name in names
        ]

    async def dispatch(self, name: str, tool_input: dict, ctx: TurnContext) -> dict:
        tool = self._tools.get(name)
        if tool is None:
            return {"error": f"Unknown tool '{name}'."}
        return await tool.handler(tool_input, ctx)
