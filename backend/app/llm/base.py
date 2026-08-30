"""Provider-neutral interface between JarvisAgent and whatever LLM actually
runs the reasoning. Nothing outside this package should import a specific
vendor SDK (anthropic, openai, ...) - agent.py talks only to LLMProvider and
the types defined here.

Message history convention: messages are plain dicts, {"role": ..., "content": ...},
following the same shape most tool-use-capable chat APIs already use (a plain
string or a list of content-block dicts). This happens to need zero translation
for Claude today; a future provider with a different wire format is responsible
for translating to/from this shape in its own adapter, not in agent.py.
"""

from dataclasses import dataclass
from typing import Any, Literal, Protocol

StopReason = Literal["tool_use", "end_turn", "max_tokens"]


@dataclass(frozen=True)
class ToolDef:
    """One tool's schema, in JSON-schema-ish terms every major provider
    understands - each adapter maps `parameters` to its own field name."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ContentBlock:
    """A normalized piece of an assistant turn: either plain text or a tool call."""

    type: Literal["text", "tool_use"]
    text: str | None = None
    id: str | None = None
    name: str | None = None
    input: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Wire representation used when appending this block to message history."""
        if self.type == "text":
            return {"type": "text", "text": self.text}
        return {"type": "tool_use", "id": self.id, "name": self.name, "input": self.input}


@dataclass(frozen=True)
class LLMResponse:
    content: list[ContentBlock]
    stop_reason: StopReason


class LLMProvider(Protocol):
    """Anything JarvisAgent can run its tool-use loop against."""

    async def generate(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[ToolDef]
    ) -> LLMResponse: ...
