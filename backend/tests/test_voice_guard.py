"""The guard and a voice Jarvis didn't recognize: switched off, or "Я дома" run while it's on,
only for a resident's voice. Typed and the PIN-locked panel aren't voices - not restricted."""

import asyncio

from app.agent import JarvisAgent
from app.db import connect
from app.domains.home import GUARD_NEEDS_A_KNOWN_VOICE, disarms, make_handlers
from app.memory import MemoryStore
from app.tools.registry import Tool, ToolRegistry, TurnContext
from tests.test_agent import FakeLLM, text_response, tool_use_response
from tests.test_home_features import FakeHA, _state


class House(FakeHA):
    def __init__(self, armed: bool):
        super().__init__()
        self.states = [s for s in self.states if s["entity_id"] != "input_boolean.security_armed"]
        self.states += [_state("input_boolean.security_armed", "on" if armed else "off", friendly_name="Охрана"),
                        _state("script.ya_doma", "off", friendly_name="Я дома"),
                        _state("script.kino", "off", friendly_name="Кино"),
                        _state("script.snyat", "off", friendly_name="Снять охрану")]
        self.scripts = {
            "ya_doma": {"description": "Фразы: я дома, я пришёл.", "sequence": [{"action": "scene.turn_on"}]},
            "kino": {"description": "Фразы: включи кино.", "sequence": [{"action": "light.turn_off"}]},
            "snyat": {"description": "Фразы: сними охрану.", "sequence": [
                {"action": "input_boolean.turn_off", "target": {"entity_id": "input_boolean.security_armed"}}]},
        }

    async def get_script_config(self, object_id):
        return self.scripts[object_id]


def _run(handler, voice, **tool_input):
    return asyncio.run(handler(tool_input, TurnContext(voice=voice)))


def test_an_unknown_voice_does_not_turn_the_guard_off_a_resident_and_the_panel_do():
    ha = House(armed=True)
    _, control, _, _ = make_handlers(ha)
    refused = _run(control, "unknown", room="дом", device="security", action="off")
    assert refused["say_exactly"] == GUARD_NEEDS_A_KNOWN_VOICE and ha.calls == []
    assert "done" in _run(control, "unknown", room="дом", device="security", action="on")  # arming is anyone's
    assert "done" in _run(control, "resident", room="дом", device="security", action="off")
    assert "done" in _run(control, None, room="дом", device="security", action="off")  # typed / the panel


def test_i_am_home_by_an_unknown_voice_while_the_house_is_guarded():
    ha = House(armed=True)
    *_, scenario = make_handlers(ha)
    assert _run(scenario, "unknown", name="я дома")["say_exactly"] == GUARD_NEEDS_A_KNOWN_VOICE
    assert _run(scenario, "unknown", name="сними охрану")["say_exactly"] == GUARD_NEEDS_A_KNOWN_VOICE  # its own step
    assert _run(scenario, "unknown", name="кино")["ran"] == "Кино"  # nothing to do with the guard
    assert _run(scenario, "resident", name="я дома")["ran"] == "Я дома"
    assert ha.calls == [("script", "kino", None, None), ("script", "ya_doma", None, None)]
    # not guarded: "Я дома" is just the norms coming back, anyone can say it
    *_, scenario = make_handlers(House(armed=False))
    assert _run(scenario, "unknown", name="я дома")["ran"] == "Я дома"


def test_which_scenarios_turn_the_guard_off():
    assert disarms("ya_doma", {})
    assert not disarms("kino", {"sequence": [{"action": "input_boolean.turn_on",
                                              "target": {"entity_id": "input_boolean.security_armed"}}]})
    assert disarms("x", {"sequence": [{"action": "input_boolean.turn_off",
                                       "target": {"entity_id": "input_boolean.security_armed"}}]})


def test_the_refusal_is_said_word_for_word_not_reworded_by_the_model(tmp_path):
    async def guard(tool_input, ctx):
        return {"error": "nope", "say_exactly": GUARD_NEEDS_A_KNOWN_VOICE} if ctx.voice == "unknown" else {"done": 1}

    tools = ToolRegistry()
    tools.register(Tool(name="guard", description="", parameters={"type": "object", "properties": {}}, handler=guard))
    llm = FakeLLM([tool_use_response("t1", "guard", {}), text_response("Охрана снята.")])
    agent = JarvisAgent(llm=llm, tools=tools, memory=MemoryStore(connect(str(tmp_path / "t.db"))))
    result = asyncio.run(agent.chat("s", "default", "сними охрану", voice="unknown"))
    assert result["response"] == GUARD_NEEDS_A_KNOWN_VOICE
    assert llm.generate.await_count == 1  # the model never got to put it its own way
    # the next turn still works: the history ends with Jarvis's words
    llm.generate.side_effect = [text_response("Пожалуйста.")]
    assert asyncio.run(agent.chat("s", "default", "спасибо"))["response"] == "Пожалуйста."


def test_who_counts_as_a_resident_for_the_guard():
    from voice_app import ASKED, CLOSER, CONFIDENT, LAST, UNKNOWN, voice_trust

    assert voice_trust(CONFIDENT, -1e9, 100) == voice_trust(CLOSER, -1e9, 100) == "resident"
    assert voice_trust(LAST, 50, 100) == "resident"  # a resident recognized a minute ago, a short "я дома" now
    assert voice_trust(LAST, 50, 500) == "unknown"  # hours later: whoever it is, not proven
    assert voice_trust(ASKED, 99, 100) == voice_trust(UNKNOWN, 99, 100) == voice_trust(None, 99, 100) == "unknown"
