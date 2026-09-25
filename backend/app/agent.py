"""JarvisAgent: the device-agnostic, llm-agnostic tool-use loop. It knows
nothing about Home Assistant, rooms, or Claude specifically - only the
LLMProvider/ToolRegistry/MemoryStore interfaces. Domain tools are registered
into the ToolRegistry from outside - build_default_agent() wires in
app.domains.computer and files (phase 2) and app.domains.home (phase 3,
Home Assistant - when HOME_ASSISTANT_TOKEN is set), see docs/TZ.md."""

from app.config import settings
from app.db import connect
from app.llm.base import LLMProvider
from app.llm.claude import ClaudeProvider
from app.llm.groq import GroqProvider
from app.llm.ollama import OllamaProvider
from app.memory import MemoryStore
from app.persona import (
    build_persona_prompt,
    gender_prompt_note,
    get_resident_gender,
    resolve_gendered_notation,
    update_resident_gender,
    update_style,
)
from app.tools.registry import ToolRegistry, TurnContext

MAX_TOOL_ITERATIONS = 8
# Only the latest turns go to the model (the session keeps everything). A
# voice session is one long conversation; sent whole, every request grew
# until Groq's free 8000 tokens a minute ran out after a few house commands,
# and replies waited 10-88 s on its rate limit.
MAX_HISTORY_TURNS = 6

GENERAL_ASSISTANT_PREAMBLE = (
    "You are Jarvis, the voice-controlled AI running inside a private home. Residents can "
    "speak with you in any room; they also have a wall-mounted iPad with the same you, but "
    "for now you are being tested over text only.\n\n"
    "Answer any question a resident asks, not only ones about the house - general knowledge, "
    "casual conversation, quick calculations, anything a good assistant would handle. Only "
    "reach for a tool when the request is actually about controlling or checking the house."
)

# Added only when the reply will be spoken (voice_app with TTS on). Local
# voice-cloning TTS takes ~10x longer to generate than the audio lasts, so
# every extra sentence costs real waiting - and markdown/emoji get read out
# literally instead of rendered.
SPOKEN_REPLY_RULES = (
    "Your reply will be read aloud by a slow text-to-speech voice, not shown as text. "
    "Answer in one or two short sentences. No markdown, no lists, no emoji, no code; "
    "don't read out file paths or URLs unless asked - just say what you did."
)


class JarvisAgent:
    """Holds one conversation per session_id and runs the tool-use loop.

    llm/tools/memory are public: tools especially is meant to be extended
    from outside (later phases register domain tools into the same
    registry instance this agent was built with).
    """

    def __init__(self, llm: LLMProvider, tools: ToolRegistry, memory: MemoryStore):
        self.llm = llm
        self.tools = tools
        self.memory = memory
        self._sessions: dict[str, list[dict]] = {}

    def reset(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def _build_system_prompt(self, resident_id: str, spoken: bool = False) -> str:
        mode = self.memory.get_persona_mode()
        persona_prompt = build_persona_prompt(mode, self.memory, resident_id)
        gender_note = gender_prompt_note(get_resident_gender(self.memory, resident_id))
        prompt = f"{GENERAL_ASSISTANT_PREAMBLE}\n\n{persona_prompt}\n\n{gender_note}"
        if spoken:
            prompt += f"\n\n{SPOKEN_REPLY_RULES}"
        return prompt

    async def chat(self, session_id: str, resident_id: str, user_message: str, spoken: bool = False) -> dict:
        self.memory.ensure_resident(resident_id)
        # Detected early (before the system prompt is built) so a message
        # that reveals gender for the first time can already inform this
        # same turn's reply, not just later ones - see persona.py.
        update_resident_gender(self.memory, resident_id, user_message)
        history = self._sessions.setdefault(session_id, [])
        history.append({"role": "user", "content": user_message})

        system_prompt = self._build_system_prompt(resident_id, spoken=spoken)
        tool_defs = self.tools.definitions()
        ctx = TurnContext()
        actions: list[dict] = []
        final_text = None

        for _ in range(MAX_TOOL_ITERATIONS):
            response = await self.llm.generate(
                system=system_prompt, messages=recent_turns(history, MAX_HISTORY_TURNS), tools=tool_defs
            )
            history.append({"role": "assistant", "content": [b.to_dict() for b in response.content]})

            if response.stop_reason != "tool_use":
                final_text = "".join(b.text or "" for b in response.content if b.type == "text")
                break

            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                result = await self.tools.dispatch(block.name, block.input, ctx)
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

        if final_text is None:
            final_text = (
                "I've hit the tool-call limit for this turn without reaching a final "
                "answer - something may be going in circles. Please rephrase or try again."
            )

        # Unconditional backstop (see persona.py) - even with the prompt
        # instruction above, the model can still slip into "сделал(а)"-style
        # notation, which reads fine but derails TTS when spoken aloud.
        final_text = resolve_gendered_notation(final_text, get_resident_gender(self.memory, resident_id))

        if self.memory.get_persona_mode() == "adaptive":
            update_style(self.memory, resident_id, user_message)

        return {"response": final_text, "actions": actions}


def recent_turns(history: list[dict], max_turns: int) -> list[dict]:
    """The tail of the history starting at the max_turns-th last thing the
    resident said - never mid-turn, so a tool result is never sent without
    the tool call it answers."""
    starts = [i for i, m in enumerate(history) if m["role"] == "user" and isinstance(m["content"], str)]
    if len(starts) <= max_turns:
        return history
    return history[starts[-max_turns]:]


def _build_llm_provider() -> LLMProvider:
    if settings.llm_provider == "claude":
        return ClaudeProvider(api_key=settings.anthropic_api_key, model=settings.anthropic_model)
    if settings.llm_provider == "ollama":
        # Free, self-hosted, CPU-friendly small model - proof of concept that
        # the core is genuinely llm-agnostic, not a production-quality swap
        # yet. See README for the tradeoffs (tool-use reliability, latency).
        return OllamaProvider(model=settings.ollama_model, base_url=settings.ollama_base_url)
    if settings.llm_provider == "groq":
        # Free tier, no credit card, open-weight models on Groq's hardware -
        # no local GPU needed and no vendor lock-in on the model itself
        # (the weights are open, unlike Claude's).
        return GroqProvider(
            api_key=settings.groq_api_key,
            model=settings.groq_model,
            base_url=settings.groq_base_url,
            temperature=settings.groq_temperature,
        )
    raise ValueError(f"Unsupported LLM_PROVIDER {settings.llm_provider!r}. Valid: claude, ollama, groq")


def build_default_agent() -> JarvisAgent:
    """Single wiring point for the production agent - main.py, chat_cli.py
    and voice_app.py all call this rather than constructing JarvisAgent
    themselves."""
    from app.domains import computer, files

    llm = _build_llm_provider()
    memory = MemoryStore(connect(settings.db_path))
    tools = ToolRegistry()
    computer.register(tools)  # phase 2: first domain, no Home Assistant needed
    files.register(tools)  # phase 2: file operations, unrestricted paths by user's choice
    if settings.home_assistant_token:
        from app.domains import home

        home.register(tools)  # phase 3: the house, through Home Assistant
    return JarvisAgent(llm=llm, tools=tools, memory=memory)
