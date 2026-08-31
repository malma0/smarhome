"""LLMProvider backed by Groq's OpenAI-compatible API - free tier, no credit
card required, serving open-weight models (openai/gpt-oss-*, qwen/*, ...) on
Groq's own hardware. Because the models are open-weight, nothing here locks
the product in the way a closed model would: the same weights can be
self-hosted later on owned/rented GPU without touching agent.py.

This is yet another wire format, distinct from both Anthropic's
(llm/claude.py) and Ollama's native one (llm/ollama.py): tool call arguments
travel as a JSON-encoded *string*, not a dict, and each tool_call is wrapped
in a "type": "function" envelope - both translated here, not in agent.py.
"""

import json
from typing import Any

import httpx

from app.llm.base import ContentBlock, LLMResponse, StopReason, ToolDef

_FINISH_REASON_MAP: dict[str, StopReason] = {
    "tool_calls": "tool_use",
    "stop": "end_turn",
    "length": "max_tokens",
}


class GroqProvider:
    def __init__(self, api_key: str, model: str, base_url: str = "https://api.groq.com/openai/v1"):
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=60, headers={"Authorization": f"Bearer {api_key}"})

    async def generate(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[ToolDef]
    ) -> LLMResponse:
        openai_messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for message in messages:
            openai_messages.extend(self._translate_message(message))

        response = await self._client.post(
            f"{self._base_url}/chat/completions",
            json={
                "model": self._model,
                "messages": openai_messages,
                "tools": [self._to_openai_tool(t) for t in tools],
            },
        )
        response.raise_for_status()
        return self._parse_response(response.json())

    @staticmethod
    def _to_openai_tool(tool: ToolDef) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": tool.name, "description": tool.description, "parameters": tool.parameters},
        }

    @staticmethod
    def _translate_message(message: dict[str, Any]) -> list[dict[str, Any]]:
        role = message["role"]
        content = message["content"]

        if isinstance(content, str):
            return [{"role": role, "content": content}]

        if role == "assistant":
            text = "".join(b["text"] for b in content if b["type"] == "text" and b.get("text"))
            tool_calls = [
                {
                    "id": b["id"],
                    "type": "function",
                    "function": {"name": b["name"], "arguments": json.dumps(b["input"])},
                }
                for b in content
                if b["type"] == "tool_use"
            ]
            out: dict[str, Any] = {"role": "assistant", "content": text or None}
            if tool_calls:
                out["tool_calls"] = tool_calls
            return [out]

        # role == "user" carrying one or more tool_result blocks
        return [
            {"role": "tool", "content": block["content"], "tool_call_id": block["tool_use_id"]}
            for block in content
        ]

    @staticmethod
    def _parse_response(data: dict[str, Any]) -> LLMResponse:
        choice = data["choices"][0]
        message = choice["message"]
        content: list[ContentBlock] = []
        if message.get("content"):
            content.append(ContentBlock(type="text", text=message["content"]))

        for call in message.get("tool_calls") or []:
            content.append(
                ContentBlock(
                    type="tool_use",
                    id=call["id"],
                    name=call["function"]["name"],
                    input=json.loads(call["function"]["arguments"]),
                )
            )

        stop_reason = _FINISH_REASON_MAP.get(choice.get("finish_reason", "stop"), "end_turn")
        return LLMResponse(content=content, stop_reason=stop_reason)
