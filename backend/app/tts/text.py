"""Turns a reply written for the screen into text meant to be spoken.

The LLM is told to avoid markdown when its reply will be spoken (see
agent.SPOKEN_REPLY_RULES), but it doesn't always comply - and a TTS voice
reads "**", backticks and emoji out literally or stumbles over them (an
actual reply was: "Файл `hello.txt` уже создан со строкой **«привет
мира»**"). This is the backstop, applied only to what's spoken; the printed
reply keeps its formatting. Numbers become words here too (app.tts.numbers).
"""

import re

from app.tts.numbers import numbers_to_words

_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_LINE_MARKER_RE = re.compile(r"^\s*(?:#+|[-*•]|\d+[.)])\s+", re.MULTILINE)
# Single-character emphasis (*word*, _word_) - only when the markers hug a
# word from outside, so "5*3" or "hello_world" survive.
_EMPHASIS_RE = re.compile(r"(?<!\w)[*_](\S(?:[^*_\n]*?\S)?)[*_](?!\w)")
_EMOJI_RE = re.compile("[\U0001f000-\U0001faff☀-➿️‍]")


def text_for_speech(text: str) -> str:
    spoken = _LINK_RE.sub(r"\1", text)
    spoken = _LINE_MARKER_RE.sub("", spoken)
    spoken = spoken.replace("**", "").replace("__", "").replace("`", "")
    spoken = _EMPHASIS_RE.sub(r"\1", spoken)
    spoken = _EMOJI_RE.sub("", spoken)
    spoken = numbers_to_words(spoken)
    return re.sub(r"\s+", " ", spoken).strip()
