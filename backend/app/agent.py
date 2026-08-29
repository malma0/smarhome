from anthropic import AsyncAnthropic

from app.config import settings
from app.prompts import build_system_prompt
from app.tools import TOOL_DEFINITIONS, dispatch

MAX_TOOL_ITERATIONS = 8


class JarvisAgent:
    """Holds one conversation per session_id and runs the Claude tool-use loop."""

    def __init__(self):
        self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._sessions: dict[str, list[dict]] = {}

    def reset(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    async def chat(self, session_id: str, user_message: str) -> dict:
        history = self._sessions.setdefault(session_id, [])
        history.append({"role": "user", "content": user_message})

        system_prompt = await build_system_prompt()
        actions: list[dict] = []

        for _ in range(MAX_TOOL_ITERATIONS):
            response = await self._client.messages.create(
                model=settings.anthropic_model,
                max_tokens=1024,
                system=system_prompt,
                tools=TOOL_DEFINITIONS,
                messages=history,
            )

            history.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                text = "".join(block.text for block in response.content if block.type == "text")
                return {"response": text, "actions": actions}

            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                result = await dispatch(block.name, block.input)
                actions.append({"tool": block.name, "input": block.input, "result": result})
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(result),
                        "is_error": "error" in result,
                    }
                )

            history.append({"role": "user", "content": tool_results})

        return {
            "response": (
                "I've hit the tool-call limit for this turn without reaching a final "
                "answer - something may be going in circles. Please rephrase or try again."
            ),
            "actions": actions,
        }


agent = JarvisAgent()
