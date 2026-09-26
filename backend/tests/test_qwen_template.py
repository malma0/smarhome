"""The prompt the own home model gets at run time must be the text it was
trained on - byte for byte (app/llm/qwen_template.py vs transformers)."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.llm import qwen_template
from app.llm.base import ToolDef
from app.llm.ollama import OllamaProvider

TOOL = {"type": "function", "function": {"name": "control_devices", "description": "Вкл/выкл",
                                         "parameters": {"type": "object", "properties": {"room": {"type": "string"}}}}}


def test_a_tool_call_turn_renders_like_qwen():
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "выключи свет"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"type": "function", "function": {"name": "control_devices", "arguments": {"room": "кухня"}}}]},
        {"role": "tool", "content": '{"done": 1}'},
        {"role": "tool", "content": '{"done": 2}'},
        {"role": "assistant", "content": "Выключила."},
    ]
    text = qwen_template.render(messages, [TOOL], add_generation_prompt=False)
    assert text.startswith("<|im_start|>system\nSYS\n\n# Tools\n")
    assert '\n{"type": "function", "function": {"name": "control_devices", "description": "Вкл/выкл"' in text
    assert text.endswith(
        "<|im_start|>user\nвыключи свет<|im_end|>\n"
        '<|im_start|>assistant\n<tool_call>\n{"name": "control_devices", "arguments": {"room": "кухня"}}\n</tool_call><|im_end|>\n'
        '<|im_start|>user\n<tool_response>\n{"done": 1}\n</tool_response>\n<tool_response>\n{"done": 2}\n</tool_response><|im_end|>\n'
        "<|im_start|>assistant\nВыключила.<|im_end|>\n"
    )


def test_calls_are_read_back_and_broken_ones_skipped():
    out = ('<tool_call>\n{"name": "control_devices", "arguments": {"room": "зал", "action": "off"}}\n</tool_call>\n'
           "<tool_call>\n{not json}\n</tool_call>")
    text, calls = qwen_template.parse(out)
    assert text == "" and calls == [{"name": "control_devices", "arguments": {"room": "зал", "action": "off"}}]
    assert qwen_template.parse("Готово.<|im_end|>") == ("Готово.", [])


def test_raw_mode_sends_the_rendered_prompt():
    provider = OllamaProvider(model="jarvis-home", qwen_raw=True)
    provider._client.post = AsyncMock(return_value=SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"response": '<tool_call>\n{"name": "t", "arguments": {"x": 1}}\n</tool_call>', "done_reason": "stop"}))
    result = asyncio.run(provider.generate(system="S", messages=[{"role": "user", "content": "hi"}],
                                           tools=[ToolDef(name="t", description="d", parameters={"type": "object"})]))
    url, body = provider._client.post.call_args.args[0], provider._client.post.call_args.kwargs["json"]
    assert url.endswith("/api/generate") and body["raw"] is True
    assert body["prompt"].endswith("<|im_start|>user\nhi<|im_end|>\n<|im_start|>assistant\n")
    assert result.stop_reason == "tool_use" and result.content[0].input == {"x": 1}


def test_same_text_as_transformers_on_the_training_set():
    """Runs where the training stack is installed (the GPU PC)."""
    transformers = pytest.importorskip("transformers")
    from training.train_lora import BASE, to_template

    try:
        tokenizer = transformers.AutoTokenizer.from_pretrained(BASE)
    except Exception as exc:  # noqa: BLE001 - no cache and no network
        pytest.skip(f"no tokenizer: {exc}")
    data = Path(__file__).parents[1] / "training" / "data" / "train.jsonl"
    for line in data.open(encoding="utf-8").readlines()[:200]:
        record = json.loads(line)
        messages = to_template(record["messages"])
        want = tokenizer.apply_chat_template(messages, tools=record["tools"], tokenize=False, add_generation_prompt=False)
        assert qwen_template.render(messages, record["tools"], add_generation_prompt=False) == want
