"""One turn of the home model: Jarvis's history and tools -> the model's
reply, with what serving needs to watch - the raw text, Ollama's own timings
and token counts, and tool calls that came out unparseable.

The prompt is the training template (app/llm/qwen_template.py) sent raw, the
same way Jarvis has always called the model (app/llm/ollama.py) - the
fine-tuned model answers a differently rendered prompt worse.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.llm import qwen_template
from app.llm.base import LLMResponse, ToolDef
from app.llm.ollama import OllamaProvider


@dataclass
class Turn:
    response: LLMResponse
    raw: str
    seconds: float  # the whole call, as the caller waited for it
    inference_seconds: float  # Ollama's own: prompt + generation
    prompt_tokens: int
    completion_tokens: int
    malformed_calls: int  # <tool_call> blocks that weren't valid JSON - dropped by the parser
    load_seconds: float = 0.0  # loading the model into memory first (after it was unloaded)
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def tokens_per_second(self) -> float:
        generation = self.timings.get("eval_seconds", 0.0)
        return self.completion_tokens / generation if generation else 0.0


class OllamaBackend:
    def __init__(self, model: str, base_url: str = "http://localhost:11434", keep_alive: str | None = None):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.keep_alive = keep_alive  # e.g. "30m" or "-1": how long Ollama keeps the model loaded after a call
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(120, connect=3), trust_env=False)

    async def turn(self, system: str, messages: list[dict[str, Any]], tools: list[ToolDef]) -> Turn:
        ollama_messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for message in messages:
            ollama_messages.extend(OllamaProvider._translate_message(message))
        prompt = qwen_template.render(ollama_messages, [OllamaProvider._to_ollama_tool(t) for t in tools])
        body: dict[str, Any] = {"model": self.model, "prompt": prompt, "raw": True, "stream": False,
                                "options": {"temperature": 0, "stop": [qwen_template.END]}}
        if self.keep_alive:
            body["keep_alive"] = self.keep_alive
        started = time.perf_counter()
        response = await self._client.post(f"{self.base_url}/api/generate", json=body)
        response.raise_for_status()
        seconds = time.perf_counter() - started
        data = response.json()
        raw = data.get("response", "")
        text, calls = qwen_template.parse(raw)
        ns = 1e9  # Ollama reports durations in nanoseconds
        timings = {"prompt_seconds": data.get("prompt_eval_duration", 0) / ns,
                   "eval_seconds": data.get("eval_duration", 0) / ns}
        return Turn(
            response=OllamaProvider._parse_response({
                "message": {"content": text, "tool_calls": [{"function": c} for c in calls]},
                "done_reason": data.get("done_reason"),
            }),
            raw=raw, seconds=seconds,
            inference_seconds=timings["prompt_seconds"] + timings["eval_seconds"],
            prompt_tokens=data.get("prompt_eval_count", 0), completion_tokens=data.get("eval_count", 0),
            malformed_calls=max(0, raw.count("<tool_call>") - len(calls)),
            load_seconds=data.get("load_duration", 0) / ns, timings=timings,
        )

    async def model_info(self) -> dict[str, Any]:
        """What Ollama serves under this name: its digest, size, quantization - or why it can't say."""
        try:
            tags = (await self._client.get(f"{self.base_url}/api/tags")).json().get("models", [])
        except (httpx.HTTPError, ValueError) as exc:
            return {"available": False, "error": repr(exc)}
        wanted = self.model if ":" in self.model else f"{self.model}:latest"
        for m in tags:
            if m.get("name") == wanted:
                details = m.get("details", {})
                return {"available": True, "digest": m.get("digest", "")[:12], "size_mb": round(m.get("size", 0) / 2**20),
                        "quantization": details.get("quantization_level"), "parameters": details.get("parameter_size")}
        return {"available": False, "error": f"Ollama has no model {wanted}"}

    async def close(self) -> None:
        await self._client.aclose()
