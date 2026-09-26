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

import asyncio
import difflib
import json
import re
import time
import uuid
from typing import Any

import httpx

from app.http_client import ssl_context
from app.llm.base import ContentBlock, LLMResponse, StopReason, ToolDef

_FINISH_REASON_MAP: dict[str, StopReason] = {
    "tool_calls": "tool_use",
    "stop": "end_turn",
    "length": "max_tokens",
}


# Free tier: 8000 tokens a minute for openai/gpt-oss-120b (x-ratelimit-limit-tokens),
# and a few house commands in a row can reach it. Groq says how long to wait;
# a short wait beats a failed voice command, a long one isn't worth holding for.
MAX_RATE_LIMIT_RETRIES = 2
MAX_RATE_LIMIT_WAIT_SECONDS = 20


def _seconds_to_wait(response: httpx.Response) -> float:
    """How long a 429 says to wait (per minute: seconds; per day: minutes)."""
    try:
        return max(1.0, float(response.headers.get("retry-after", "60")))
    except ValueError:
        return 60.0


_GLUED_SENTENCE = re.compile(r"[.!?…](?=[A-ZА-ЯЁ])")


def _drop_repeated_answer(text: str) -> str:
    """gpt-oss on Groq sometimes writes its answer twice, the second copy
    glued straight on: "…кухня, 25.5 °C.Самая тёплая комната сейчас — кухня,
    25,5 °C." (seen live, 3 of 10 house commands). A sentence end followed
    by a capital with no space, where both sides say nearly the same thing -
    keep the second copy (it's the model's revision: "25,5" over "25.5")."""
    for match in _GLUED_SENTENCE.finditer(text):
        first, second = text[: match.end()], text[match.end() :]
        if difflib.SequenceMatcher(None, first, second).ratio() > 0.75:
            return second
    return text


# Three or more Latin words in a row whose sentence ends glued straight onto
# a Russian capital - the leak's signature ("correct.Поставила"); a normal
# sentence after an English title has a space ("Go On. Что дальше?").
_LATIN_THOUGHT = re.compile(r"[A-Za-z][A-Za-z']*(?:[ ,:;'-]+[A-Za-z][A-Za-z']*){2,}[^.!?\n]*[.!?](?=[А-ЯЁ])")


def _drop_leaked_reasoning(text: str) -> str:
    """gpt-oss on Groq sometimes puts its own thinking into the answer, the
    real answer glued on after it - seen live: "Поставила ставку?... Oops,
    need correct.Поставила воспроизведение на паузу." and "Найдено ... We
    need short answer: opened YouTube search.Открыла поиск на YouTube...".
    Keep what comes after the last English thought, if that's Russian; an
    answer that merely quotes an English title stays as it is."""
    matches = list(_LATIN_THOUGHT.finditer(text))
    if not matches:
        return text
    rest = text[matches[-1].end():].strip()
    cyrillic = sum("а" <= c.lower() <= "я" or c.lower() == "ё" for c in rest)
    return rest if rest and cyrillic >= len(rest) * 0.4 else text


def _recover_rejected_tool_call(response: httpx.Response) -> LLMResponse | None:
    """Groq checks tool call arguments against the schema itself and turns
    a mismatch into a 400 - seen live: "где жарче всего?" came back as
    get_home_status with room: null against a string schema, and the whole
    turn failed. The call it rejected is in the error ("failed_generation");
    it's used as is - the tool handlers check their own input anyway."""
    if response.status_code != 400:
        return None
    try:
        error = response.json()["error"]
        if error.get("code") != "tool_use_failed":
            return None
        call = json.loads(error["failed_generation"])
        name, arguments = call["name"], call.get("arguments") or {}
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
    except (ValueError, KeyError, TypeError):
        return None
    arguments = {k: v for k, v in arguments.items() if v is not None}
    block = ContentBlock(type="tool_use", id=f"recovered_{uuid.uuid4().hex[:12]}", name=name, input=arguments)
    return LLMResponse(content=[block], stop_reason="tool_use")


class GroqProvider:
    # Groq's own API default is 1.0 - observed live to occasionally produce
    # repetitive/garbled text ("Выполняем только только пере-перелё-пер пер
    # пер...") on this model, especially right after a multi-tool-call turn.
    # A lower default doesn't eliminate that risk (it's the model, not a
    # bug in this file), but noticeably reduces how often it happens.
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.groq.com/openai/v1",
        temperature: float = 0.4,
        fallback_models: tuple[str, ...] = (),
    ):
        self._model = model
        # Each model has its own free limits (8 000 tokens a minute, 200 000 a
        # day on Groq) - when one runs out, the next one answers.
        self._models = [model, *(m for m in fallback_models if m and m != model)]
        self._resting_until: dict[str, float] = {}  # model -> monotonic time its limit frees up
        self._last_refusal: dict[str, httpx.Response] = {}
        self.last_model: str | None = None
        self._base_url = base_url.rstrip("/")
        self._temperature = temperature
        self._client = httpx.AsyncClient(
            timeout=60, headers={"Authorization": f"Bearer {api_key}"}, verify=ssl_context()
        )

    async def generate(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[ToolDef]
    ) -> LLMResponse:
        openai_messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for message in messages:
            openai_messages.extend(self._translate_message(message))

        openai_tools = [self._to_openai_tool(t) for t in tools]
        response = None
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            model = self._pick_model()
            if model is None:  # every model is resting: the one that frees up first says when
                soonest = min(self._resting_until, key=self._resting_until.get)
                self._last_refusal[soonest].raise_for_status()
            response = await self._client.post(f"{self._base_url}/chat/completions",
                                               json=self._body(model, openai_messages, openai_tools))
            if response.status_code != 429:
                self.last_model = model
                break
            wait = _seconds_to_wait(response)
            self._resting_until[model] = time.monotonic() + wait
            self._last_refusal[model] = response
            if self._pick_model() is not None:
                continue  # another model has its own limits - no waiting
            if wait > MAX_RATE_LIMIT_WAIT_SECONDS or attempt == MAX_RATE_LIMIT_RETRIES:
                break
            await asyncio.sleep(wait)  # the only model - a short wait beats a failed voice command
            self._resting_until.pop(model, None)
        if recovered := _recover_rejected_tool_call(response):
            return recovered
        response.raise_for_status()
        return self._parse_response(response.json())

    def _pick_model(self) -> str | None:
        now = time.monotonic()
        return next((m for m in self._models if self._resting_until.get(m, 0) <= now), None)

    def _body(self, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        body = {"model": model, "messages": messages, "tools": tools, "temperature": self._temperature}
        if model.startswith("openai/gpt-oss"):
            # Same tool calls, fewer tokens (measured 50 vs 78 per call) -
            # which is what the free tier's per-minute budget runs out of.
            body["reasoning_effort"] = "low"
        elif "qwen" in model:
            body["reasoning_format"] = "hidden"  # Qwen3 can think out loud into the answer
        return body

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
            text = _drop_repeated_answer(_drop_leaked_reasoning(message["content"]))
            content.append(ContentBlock(type="text", text=text))

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
