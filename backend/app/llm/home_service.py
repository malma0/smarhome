"""LLMProvider for the home model served by model_service (HOME_LLM_SERVICE_URL):
the same turn JarvisAgent would send Ollama, over the service's /v1/generate,
so the model's speed and behavior are measured in one place. Errors are httpx
errors like Ollama's - the agent then hands the house to the main model.
"""

from typing import Any

import httpx

from app.http_client import trust_env
from app.llm.base import ContentBlock, LLMResponse, ToolDef


class HomeServiceProvider:
    def __init__(self, base_url: str = "http://localhost:8090", transport: httpx.AsyncBaseTransport | None = None):
        self._base_url = base_url.rstrip("/")
        # the service down shouldn't hold a command: 3 s to connect, 120 to answer (a cold model loads first)
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(120, connect=3), trust_env=trust_env(self._base_url),
                                         transport=transport)

    async def generate(self, *, system: str, messages: list[dict[str, Any]], tools: list[ToolDef]) -> LLMResponse:
        response = await self._client.post(f"{self._base_url}/v1/generate", json={
            "system": system, "messages": messages,
            "tools": [{"name": t.name, "description": t.description, "parameters": t.parameters} for t in tools],
        })
        response.raise_for_status()
        data = response.json()
        content = [ContentBlock(type="text", text=b.get("text")) if b["type"] == "text"
                   else ContentBlock(type="tool_use", id=b.get("id"), name=b.get("name"), input=b.get("input") or {})
                   for b in data["content"]]
        return LLMResponse(content=content, stop_reason=data["stop_reason"])
