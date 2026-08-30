"""LLMProvider implementation backed by the Anthropic API. This is the only
file in the codebase (besides its test) allowed to import `anthropic`."""

from typing import Any

from anthropic import AsyncAnthropic

from app.llm.base import ContentBlock, LLMResponse, StopReason, ToolDef

_STOP_REASON_MAP: dict[str, StopReason] = {
    "tool_use": "tool_use",
    "end_turn": "end_turn",
    "max_tokens": "max_tokens",
    "stop_sequence": "end_turn",
}


class ClaudeProvider:
    def __init__(self, api_key: str, model: str):
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model

    async def generate(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[ToolDef]
    ) -> LLMResponse:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=system,
            tools=[self._to_anthropic_tool(t) for t in tools],
            messages=messages,
        )
        content = [self._from_anthropic_block(b) for b in response.content]
        stop_reason = _STOP_REASON_MAP.get(response.stop_reason or "end_turn", "end_turn")
        return LLMResponse(content=content, stop_reason=stop_reason)

    @staticmethod
    def _to_anthropic_tool(tool: ToolDef) -> dict[str, Any]:
        return {"name": tool.name, "description": tool.description, "input_schema": tool.parameters}

    @staticmethod
    def _from_anthropic_block(block: Any) -> ContentBlock:
        if block.type == "text":
            return ContentBlock(type="text", text=block.text)
        if block.type == "tool_use":
            return ContentBlock(type="tool_use", id=block.id, name=block.name, input=block.input)
        raise ValueError(f"Unsupported Anthropic content block type: {block.type!r}")
