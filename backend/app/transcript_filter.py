"""Known Whisper hallucinations in Russian, dropped before Jarvis answers.

Whisper was trained on huge amounts of subtitled video, so on noise or
near-silence it "hears" the credits and sign-offs those subtitles ended
with. Seen live in this project: room noise that passed the voice detector
came back as "Продолжение следует..." - and Jarvis replied to it. The same
family is well known for Russian: "Субтитры сделал DimaTorzok",
"Редактор субтитров А.Семкин Корректор А.Егорова", "Спасибо за просмотр".

Only unambiguous ones are listed. "Спасибо." also comes back from silence,
but it's a perfectly real thing to say to Jarvis, so it can't be filtered.
"""

import re

# The whole utterance is exactly one of these (after normalizing).
_WHOLE = {
    "продолжение следует",
    "спасибо за просмотр",
    "спасибо за внимание и до новых встреч",
    "подписывайтесь на канал",
    "ставьте лайки и подписывайтесь на канал",
    "до новых встреч",
}

# Anywhere in the utterance - nobody says these to a home assistant.
_MARKERS = ("dimatorzok", "субтитр", "семкин", "егорова", "подписывайтесь на канал")


def _normalize(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


def is_hallucination(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    return normalized in _WHOLE or any(marker in normalized for marker in _MARKERS)
