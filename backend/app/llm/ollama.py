"""LLMProvider implementation backed by a local Ollama server - free and
self-hosted, in exchange for running a small model on CPU. This exists as a
proof of concept that the llm-agnostic core actually works with a second
provider, not as a production replacement for ClaudeProvider yet: a 1.5B
model is noticeably less reliable at tool use, and CPU inference is slower
than a hosted API. See README for the tradeoffs and how to try it.
"""

import uuid
from typing import Any

import httpx

from app.llm import qwen_template
from app.llm.base import ContentBlock, LLMResponse, StopReason, ToolDef


class OllamaProvider:
    def __init__(self, model: str, base_url: str = "http://localhost:11434", qwen_raw: bool = False):
        """qwen_raw: build the prompt here (app/llm/qwen_template.py) instead of
        Ollama's template - for the own fine-tuned model, trained on exactly that text."""
        self._model = model
        self._qwen_raw = qwen_raw
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=120)

    async def generate(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[ToolDef]
    ) -> LLMResponse:
        ollama_messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for message in messages:
            ollama_messages.extend(self._translate_message(message))
        if self._qwen_raw:
            return await self._generate_raw(ollama_messages, tools)

        response = await self._client.post(
            f"{self._base_url}/api/chat",
            json={
                "model": self._model,
                "stream": False,
                "messages": ollama_messages,
                "tools": [self._to_ollama_tool(t) for t in tools],
                # Ollama samples at 0.8 by default; a small model then drops arguments
                # ("action" missing in 10 of 103 exam cases). Calls should be what it was taught.
                "options": {"temperature": 0},
            },
        )
        response.raise_for_status()
        return self._parse_response(response.json())

    async def _generate_raw(self, messages: list[dict[str, Any]], tools: list[ToolDef]) -> LLMResponse:
        prompt = qwen_template.render(messages, [self._to_ollama_tool(t) for t in tools])
        response = await self._client.post(
            f"{self._base_url}/api/generate",
            json={"model": self._model, "prompt": prompt, "raw": True, "stream": False,
                  "options": {"temperature": 0, "stop": [qwen_template.END]}},
        )
        response.raise_for_status()
        data = response.json()
        text, calls = qwen_template.parse(data.get("response", ""))
        return self._parse_response({
            "message": {"content": text, "tool_calls": [{"function": c} for c in calls]},
            "done_reason": data.get("done_reason"),
        })

    @staticmethod
    def _to_ollama_tool(tool: ToolDef) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": tool.name, "description": tool.description, "parameters": tool.parameters},
        }

    @staticmethod
    def _translate_message(message: dict[str, Any]) -> list[dict[str, Any]]:
        """Our history entries follow the Anthropic-shaped wire format from
        llm/base.py; Ollama's chat API wants OpenAI-style messages instead
        (assistant tool_calls inline, tool results as their own role:'tool'
        messages) - that translation lives here, not in agent.py."""
        role = message["role"]
        content = message["content"]

        if isinstance(content, str):
            return [{"role": role, "content": content}]

        if role == "assistant":
            text = "".join(b["text"] for b in content if b["type"] == "text" and b.get("text"))
            tool_calls = [
                {"id": b["id"], "function": {"name": b["name"], "arguments": b["input"]}}
                for b in content
                if b["type"] == "tool_use"
            ]
            out: dict[str, Any] = {"role": "assistant", "content": text}
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
        message = data["message"]
        content: list[ContentBlock] = []
        if message.get("content"):
            content.append(ContentBlock(type="text", text=message["content"]))

        tool_calls = message.get("tool_calls") or []
        for call in tool_calls:
            content.append(
                ContentBlock(
                    type="tool_use",
                    id=call.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                    name=call["function"]["name"],
                    input=call["function"]["arguments"],
                )
            )

        stop_reason: StopReason = "tool_use" if tool_calls else "end_turn"
        if not tool_calls and data.get("done_reason") == "length":
            stop_reason = "max_tokens"
        return LLMResponse(content=content, stop_reason=stop_reason)
