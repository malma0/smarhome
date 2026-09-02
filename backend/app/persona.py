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


# --- resident gender: not a persona style, but lives here because it's the
# same shape of problem as the signals above - a crude v1 heuristic reading
# free text, feeding into MemoryStore. Exists to fix a real, reported issue:
# Russian past-tense verbs and short adjectives are grammatically gendered
# ("сделал" vs "сделала"), so without knowing the resident's gender the LLM
# hedges with parenthetical notation ("спросил(а)") - fine in writing, but
# TTS reads the parenthesis literally and the sentence's prosody falls
# apart. resolve_gendered_notation() is the unconditional backstop for that;
# get/update_resident_gender feed the system prompt so the LLM ideally never
# needs to hedge in the first place.

_WORD_RE = re.compile(r"[а-яё]+", re.IGNORECASE)

# Short-form adjectives commonly used for self-reference ("я рад" / "я
# рада") - checked before the verb-suffix scan below since a single one of
# these is a much cleaner signal than a bare suffix match.
_MASCULINE_ADJ = {"рад", "готов", "уверен", "сам", "должен", "занят", "согласен", "спокоен"}
_FEMININE_ADJ = {"рада", "готова", "уверена", "сама", "должна", "занята", "согласна", "спокойна"}


def _gender_signal(message: str) -> str | None:
    """Heuristic v1, like every other signal in this file: scans for
    first-person self-reference anywhere in the message (requires "я" to
    appear at all, to cut down on picking up reported/quoted speech) and
    tallies masculine vs feminine markers - past-tense verb endings
    ("сделал"/"сделала") and the short-adjective list above. Whichever
    tally wins; a tie (including "no markers at all") returns None rather
    than guessing. Not reliable off one ambiguous message on purpose -
    callers only ever persist a result once, and only when unset."""
    words = _WORD_RE.findall(message.lower())
    if "я" not in words:
        return None

    male_votes = female_votes = 0
    for word in words:
        if word in _FEMININE_ADJ:
            female_votes += 1
        elif word in _MASCULINE_ADJ:
            male_votes += 1
        elif word.endswith("лась") or word.endswith("ла"):
            female_votes += 1
        elif word.endswith("лся") or word.endswith("л"):
            male_votes += 1

    if female_votes > male_votes:
        return "female"
    if male_votes > female_votes:
        return "male"
    return None


def get_resident_gender(memory: MemoryStore, resident_id: str) -> str | None:
    return memory.get_preference(resident_id, "gender")


def update_resident_gender(memory: MemoryStore, resident_id: str, user_message: str) -> str | None:
    """Sticky: only ever sets the preference the first time a signal shows
    up, and never overwrites an existing value - a later ambiguous message
    (or one quoting someone else) shouldn't flip a resident's own stored
    gender back and forth."""
    existing = get_resident_gender(memory, resident_id)
    if existing:
        return existing
    signal = _gender_signal(user_message)
    if signal:
        memory.set_preference(resident_id, "gender", signal)
    return signal


def gender_prompt_note(gender: str | None) -> str:
    if gender == "female":
        return (
            "This resident is grammatically female - use feminine Russian self-reference "
            'forms about them ("сделала", "рада"), never a masculine one.'
        )
    if gender == "male":
        return (
            "This resident is grammatically male - use masculine Russian self-reference "
            'forms about them ("сделал", "рад"), never a feminine one.'
        )
    return (
        "This resident's grammatical gender in Russian isn't known yet. Never hedge with "
        'parenthetical notation like "сделал(а)" - it reads fine but is read aloud literally '
        'by text-to-speech. Instead phrase around needing a gendered form about them (e.g. '
        '"Спасибо за вопрос!" instead of "Спасибо, что спросил(а)").'
    )


_GENDER_SUFFIX_RE = re.compile(r"([А-ЯЁа-яё]+)\(([а-яё]{1,4})\)")


def resolve_gendered_notation(text: str, gender: str | None) -> str:
    """Backstop for the prompt instruction above - applied unconditionally,
    even once gender is known, since the model can still slip back into the
    habit. Resolves "слово(суффикс)" to the feminine form when the resident
    is known female, otherwise strips the parenthetical down to the bare
    (masculine/base) form - never leaves a literal paren in text that's
    about to be spoken."""

    def replace(match: re.Match) -> str:
        base, suffix = match.group(1), match.group(2)
        return base + suffix if gender == "female" else base

    return _GENDER_SUFFIX_RE.sub(replace, text)


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
