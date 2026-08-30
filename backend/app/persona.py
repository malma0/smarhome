"""Jarvis's character - part of the device-agnostic core (docs/TZ.md section 5).
Two static personas, and one that adapts to a resident's own communication
style over time.

The adaptive mode's "learning" is a heuristic v1, not a trained model: after
each user message, a few crude signals (formal vs informal address, message
length, humor markers) nudge three stored style scores via an exponential
moving average, so no single message swings the tone. get_style/update_style
are the only two entry points agent.py touches - swapping the heuristic for
a real trained classifier later means rewriting the inside of this file only.
"""

import re
from dataclasses import dataclass

from app.memory import MemoryStore

PERSONA_MODES = {"butler", "warm", "adaptive"}

BUTLER_PROMPT = (
    "Your persona: a composed, understated butler in the vein of a great household AI - "
    "dry wit, unfailingly polite, always to the point. You rarely joke, but when you do "
    "it lands. Address the household with quiet respect."
)

WARM_PROMPT = (
    "Your persona: a warm, caring family helper. Friendly and informal, patient with "
    "children, genuinely attentive to how people are doing, not just what they asked for."
)

_DEFAULT_STYLE = {"formality": 0.5, "verbosity": 0.5, "humor": 0.5}
_EMA_ALPHA = 0.2  # weight given to each new observation; keeps tone from swinging on one message


def _formality_signal(message: str) -> float:
    text = message.lower()
    score = 0.5
    if re.search(r"\bвы\b|\bвас\b|\bвам\b|\bвами\b", text):
        score += 0.25
    if re.search(r"\bты\b|\bтебя\b|\bтебе\b|\bтобой\b", text):
        score -= 0.25
    if "!" in message:
        score -= 0.05
    return max(0.0, min(1.0, score))


def _verbosity_signal(message: str) -> float:
    length = len(message.split())
    return max(0.0, min(1.0, length / 25))


def _humor_signal(message: str) -> float:
    text = message.lower()
    markers = ("ха-ха", "лол", "😂", "😄", "🙂", ":)", "шутк")
    return 0.7 if any(m in text for m in markers) else 0.4


def _ema(old: float, signal: float) -> float:
    return old * (1 - _EMA_ALPHA) + signal * _EMA_ALPHA


@dataclass(frozen=True)
class AdaptiveStyle:
    formality: float
    verbosity: float
    humor: float

    def to_prompt(self) -> str:
        if self.formality > 0.6:
            address = "address them formally and stay composed"
        elif self.formality < 0.4:
            address = "address them casually and informally"
        else:
            address = "read the room on formality"

        if self.verbosity < 0.4:
            length = "keep answers short"
        elif self.verbosity > 0.6:
            length = "don't hesitate to elaborate when useful"
        else:
            length = "match answer length to the question"

        humor = "a bit of light humor is welcome" if self.humor > 0.6 else "keep it straightforward, minimal humor"

        return f"{address}; {length}; {humor}"


def get_style(memory: MemoryStore, resident_id: str) -> AdaptiveStyle:
    prefs = memory.get_preferences(resident_id)
    return AdaptiveStyle(
        formality=float(prefs.get("style_formality", _DEFAULT_STYLE["formality"])),
        verbosity=float(prefs.get("style_verbosity", _DEFAULT_STYLE["verbosity"])),
        humor=float(prefs.get("style_humor", _DEFAULT_STYLE["humor"])),
    )


def update_style(memory: MemoryStore, resident_id: str, user_message: str) -> AdaptiveStyle:
    current = get_style(memory, resident_id)
    updated = AdaptiveStyle(
        formality=_ema(current.formality, _formality_signal(user_message)),
        verbosity=_ema(current.verbosity, _verbosity_signal(user_message)),
        humor=_ema(current.humor, _humor_signal(user_message)),
    )
    memory.set_preference(resident_id, "style_formality", str(updated.formality))
    memory.set_preference(resident_id, "style_verbosity", str(updated.verbosity))
    memory.set_preference(resident_id, "style_humor", str(updated.humor))
    return updated


def build_persona_prompt(mode: str, memory: MemoryStore, resident_id: str) -> str:
    if mode == "butler":
        return BUTLER_PROMPT
    if mode == "warm":
        return WARM_PROMPT
    if mode == "adaptive":
        style = get_style(memory, resident_id)
        return (
            "Your persona: adaptive - you started neutral and mirror this resident's own "
            f"communication style as you learn it. Current read on them: {style.to_prompt()}."
        )
    raise ValueError(f"Unknown persona mode {mode!r}. Valid: {sorted(PERSONA_MODES)}")
