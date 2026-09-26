"""Qwen2.5's chat template in Python: the exact text the own home model was
trained on (training/train_lora.py renders it with transformers).

Ollama renders tools its own way - properties sorted by name, fields like
"minimum" dropped - and the fine-tuned 1.5B model reacted to that: it left
"action" out of calls it had seen with "action" 1734 times. So for Qwen
models the prompt is built here and sent to Ollama raw.
"""

import json
import re
from typing import Any

DEFAULT_SYSTEM = "You are Qwen, created by Alibaba Cloud. You are a helpful assistant."
TOOLS_HEAD = (
    "\n\n# Tools\n\nYou may call one or more functions to assist with the user query.\n\n"
    "You are provided with function signatures within <tools></tools> XML tags:\n<tools>"
)
TOOLS_TAIL = (
    "\n</tools>\n\nFor each function call, return a json object with function name and arguments within "
    '<tool_call></tool_call> XML tags:\n<tool_call>\n{"name": <function-name>, "arguments": <args-json-object>}\n'
    "</tool_call><|im_end|>\n"
)
END = "<|im_end|>"
_TOOL_CALL = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.S)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)  # transformers' tojson


def render(messages: list[dict], tools: list[dict], add_generation_prompt: bool = True) -> str:
    """messages in OpenAI shape (system, user, assistant with tool_calls, tool);
    tools as {"type": "function", "function": {...}}."""
    has_system = bool(messages) and messages[0]["role"] == "system"
    system = messages[0]["content"] if has_system else DEFAULT_SYSTEM
    if tools:
        out = "<|im_start|>system\n" + system + TOOLS_HEAD
        out += "".join("\n" + _json(tool) for tool in tools)
        out += TOOLS_TAIL
    else:
        out = "<|im_start|>system\n" + system + END + "\n"

    for i, m in enumerate(messages):
        role = m["role"]
        if role == "system" and i == 0:
            continue
        if role in ("user", "system") or (role == "assistant" and not m.get("tool_calls")):
            out += f"<|im_start|>{role}\n{m.get('content') or ''}{END}\n"
        elif role == "assistant":
            out += "<|im_start|>assistant"
            if m.get("content"):
                out += "\n" + m["content"]
            for call in m["tool_calls"]:
                call = call.get("function", call)
                out += '\n<tool_call>\n{"name": "' + call["name"] + '", "arguments": ' + _json(call["arguments"]) + "}\n</tool_call>"
            out += END + "\n"
        elif role == "tool":
            if i == 0 or messages[i - 1]["role"] != "tool":
                out += "<|im_start|>user"
            out += "\n<tool_response>\n" + m["content"] + "\n</tool_response>"
            if i == len(messages) - 1 or messages[i + 1]["role"] != "tool":
                out += END + "\n"

    if add_generation_prompt:
        out += "<|im_start|>assistant\n"
    return out


def parse(generated: str) -> tuple[str, list[dict]]:
    """The model's turn -> (text, [{"name", "arguments"}]). A call that isn't
    valid JSON is left out rather than guessed at."""
    generated = generated.split(END)[0]
    calls = []
    for raw in _TOOL_CALL.findall(generated):
        try:
            call = json.loads(raw)
            calls.append({"name": call["name"], "arguments": call.get("arguments") or {}})
        except (ValueError, KeyError, TypeError):
            continue
    text = _TOOL_CALL.sub("", generated).replace("<tool_call>", "").strip()
    return text, calls
