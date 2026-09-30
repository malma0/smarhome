"""Fresh answers from the internet - "кто выиграл вчера", "сколько стоит
айфон", "что нового". The chat model knows only what it was trained on.

Groq's gpt-oss models have a built-in browser_search tool: the model itself
searches and reads pages, then answers. Measured: ~9 s and ~16 000 tokens a
question, so it runs as a tool of its own on the fallback model (Jarvis's
main one keeps its daily budget), and only for questions that need the web.
"""

import re
from datetime import datetime

from app.config import settings
from app.http_client import shared_client
from app.tools.registry import Tool, ToolRegistry, TurnContext

MODELS = ("openai/gpt-oss-20b", "openai/gpt-oss-120b")  # the second only when the first is out
_CITATION = re.compile(r"[ \t]*【[^】]*】")  # the search's source marks, "【1†L32-L38】"


def clean(text: str) -> str:
    """For reading out: no source marks, no markdown."""
    text = _CITATION.sub("", text)
    text = re.sub(r"\*\*|__|^#+\s*|^\s*[-*]\s+", "", text, flags=re.M)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r" +$", "", text, flags=re.M).strip()


def _prompt(now: datetime) -> str:
    return (f"Today is {now:%Y-%m-%d} ({now:%A}), local time {now:%H:%M}, in {settings.weather_city or 'Russia'}. "
            "Search the web and answer in Russian in 1-3 short spoken sentences: the facts, numbers and dates "
            "asked for, nothing else. If nothing reliable is found, say so plainly.")


async def _default_post(body: dict):
    return await shared_client().post(f"{settings.groq_base_url}/chat/completions", json=body, timeout=90,
                                      headers={"Authorization": f"Bearer {settings.groq_api_key}"})


async def browse(instructions: str, question: str, post=None, max_tokens: int = 700) -> dict:
    """The model searches the web for question, told how to answer by
    instructions. {"text": what it wrote} or {"error": ...}."""
    for model in MODELS:
        body = {"model": model, "reasoning_effort": "low", "max_completion_tokens": max_tokens,
                "tools": [{"type": "browser_search"}],
                "messages": [{"role": "system", "content": instructions}, {"role": "user", "content": question}]}
        try:
            response = await (post or _default_post)(body)
        except Exception as exc:  # noqa: BLE001 - no internet is an answer, not a crash
            return {"error": f"The search didn't answer: {exc!r}"[:200]}
        if response.status_code == 429:  # this model is out of its limit - the next one
            continue
        if response.status_code != 200:
            return {"error": f"The search failed ({response.status_code})."}
        text = response.json()["choices"][0]["message"].get("content") or ""
        return {"text": text} if text.strip() else {"error": "Nothing came back from the search."}
    return {"error": "The web search is out of its free limit for now - try later."}


def make_handler(post=None, now=lambda: datetime.now().astimezone()):
    async def web_answer(tool_input: dict, ctx: TurnContext) -> dict:
        question = (tool_input.get("question") or "").strip()
        if not question:
            return {"error": "What to find out?"}
        found = await browse(_prompt(now()), question, post)
        return {"answer": clean(found["text"])} if "text" in found else found

    return web_answer


def register(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            name="web_answer",
            description=(
                "Finds out on the internet and returns a short answer to tell: news, prices of things, sports "
                "results, events, anything recent or after your training. question: in Russian, complete, with "
                "place and time if they matter. Takes ~10 s - only when your own knowledge can't be current. "
                "Not for opening a page for the resident (that's search_web)."
            ),
            parameters={
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
            },
            handler=make_handler(),
        )
    )
