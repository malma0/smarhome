"""The people side of the panel's Settings: the residents and their voices, guests with
codes for a while, and whether Jarvis answers aloud.

- A resident's voice is learned from the phone's recording the way the computer's
  window learns it (voice_app: the phrase cut into pieces, each a profile sample, the
  audio kept in voiceprints/ - this home's own, never leaves the machine).
- A guest gets a 6-digit code that opens the panel until a moment: they can see and
  switch things and the guard, not change how the house is set up (app/panel.py).
  Kept in backend/guests.json, gitignored.
- Jarvis's voice: JARVIS_TTS_ENABLED, changed at once and written back to .env.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import find_dotenv

from app.config import settings

GUESTS_FILE = Path(__file__).resolve().parent.parent / "guests.json"
HIDDEN = {"default", "panel"}  # Jarvis's own ids, not people
GUEST_HOURS = (4, 24, 72, 168)  # the editor's choices: till tonight, a day, three days, a week
# Read one at a time - the window and the phone both step through them, a phrase counted once it's heard.
# Each is ~3-5 s of speech: long enough for a voice sample (voice_app.ENROLL_MIN_SPEECH_SECONDS), short
# enough to stay one sample; ten of them fill a profile (speaker_id.MAX_ENROLLED_SAMPLES).
READ_LINES = ["Привет, Джарвис! Меня зовут {name}, запомни мой голос.",
              "Сегодня на улице тепло, и вечером мы пойдём гулять в парк.",
              "Включи, пожалуйста, свет на кухне и поставь чайник.",
              "Джарвис, какая сейчас температура в спальне и на улице?",
              "Напомни мне завтра в восемь утра позвонить маме.",
              "Добавь в список покупок молоко, хлеб, сыр и яблоки.",
              "Сделай в гостиной потеплее и закрой шторы, уже темнеет.",
              "Мы уезжаем на выходные, поставь дом на охрану.",
              "Включи какую-нибудь спокойную музыку и сделай свет потемнее.",
              "Спасибо, Джарвис, на сегодня всё. Спокойной ночи!"]


# ---------------------------------------------------------------- residents and their voices

def _speaker_id():
    """app.speaker_id set to the model Jarvis recognizes voices with (SPEAKER_MODEL) - the panel can run
    before the voice loop, or without it, and the default is the old model: Эля's ECAPA profile showed
    as "not recorded", and a voice read into the phone would have gone into the old one."""
    from app import speaker_id

    if speaker_id.active_model() is not speaker_id.MODELS.get(settings.speaker_model, speaker_id.active_model()):
        speaker_id.configure(settings.speaker_model)
    return speaker_id


def residents(memory) -> list[dict]:
    speaker_id = _speaker_id()
    enrolled = speaker_id.load_enrolled_voiceprints(memory)
    return [{"name": r, "samples": len(enrolled.get(r, []))}
            for r in sorted(memory.list_resident_ids()) if r not in HIDDEN]


def add_resident(memory, name: str) -> str:
    name = " ".join(str(name or "").split())[:40]
    if not name:
        raise ValueError("нужно имя")
    if name.casefold() in {r.casefold() for r in memory.list_resident_ids()} or name.casefold() in HIDDEN:
        raise ValueError("такой жилец уже есть")
    memory.ensure_resident(name, name)
    return name


def remove_resident(memory, name: str) -> None:
    """Gone from Jarvis's memory with their voice profile; their recordings moved to voiceprints/_aside/,
    not deleted - a profile can be rebuilt from them if it was a mistake."""
    speaker_id = _speaker_id()
    if name in HIDDEN or name not in memory.list_resident_ids():
        raise ValueError("нет такого жильца")
    memory.remove_resident(name)
    folder = speaker_id.VOICEPRINTS_DIR / speaker_id._folder_name(name)
    if folder.is_dir():
        aside = speaker_id.VOICEPRINTS_DIR / "_aside" / (folder.name + datetime.now().strftime("-%Y%m%d-%H%M%S"))
        aside.parent.mkdir(exist_ok=True)
        folder.rename(aside)


def learn_voice(memory, name: str, wav: bytes) -> int:
    """The phone's recording of one phrase -> samples in name's voice profile; how many were added."""
    import io
    import wave

    import numpy as np

    import voice_app

    if name not in memory.list_resident_ids():
        raise ValueError("нет такого жильца")
    _speaker_id()
    with wave.open(io.BytesIO(wav)) as w:
        if w.getframerate() != voice_app.SAMPLE_RATE or w.getsampwidth() != 2:
            raise ValueError("запись не в том формате")
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return voice_app.enroll_from_phrase(memory, name, [audio.reshape(-1, 1)])


# ---------------------------------------------------------------- guests

def _load() -> list[dict]:
    try:
        return json.loads(GUESTS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save(guests: list[dict]) -> None:
    GUESTS_FILE.write_text(json.dumps(guests, ensure_ascii=False, indent=1), encoding="utf-8")


def guests(now: datetime) -> list[dict]:
    """The codes still good, the soonest to end first; the ended ones are dropped from the file."""
    live = [g for g in _load() if datetime.fromisoformat(g["until"]) > now]
    if len(live) != len(_load()):
        _save(live)
    return sorted(live, key=lambda g: g["until"])


def add_guest(name: str, hours: int, now: datetime, owner_pin: str) -> dict:
    name = " ".join(str(name or "").split())[:40]
    if not name:
        raise ValueError("нужно имя гостя")
    if int(hours) not in GUEST_HOURS:
        raise ValueError("на сколько: 4 часа, сутки, 3 дня или неделя")
    taken = {g["code"] for g in guests(now)} | {owner_pin}
    code = ""
    while not code or code in taken:
        code = f"{secrets.randbelow(10 ** 6):06d}"
    guest = {"code": code, "name": name, "until": (now + timedelta(hours=int(hours))).isoformat(timespec="minutes"),
             "made": now.isoformat(timespec="minutes")}
    _save(guests(now) + [guest])
    return guest


def remove_guest(code: str, now: datetime) -> None:
    _save([g for g in guests(now) if g["code"] != code])


def guest_by_code(code: str, now: datetime) -> dict | None:
    return next((g for g in guests(now) if secrets.compare_digest(g["code"], str(code))), None) if code else None


# ---------------------------------------------------------------- Jarvis's voice

def voice() -> dict:
    return {"enabled": settings.tts_enabled, "provider": settings.tts_provider, "profile": settings.voicebox_profile}


def set_voice(enabled: bool, env_path: str | None = None) -> dict:
    """Aloud or not, at once (the voice loop reads the setting for every answer) and in .env for the next start."""
    object.__setattr__(settings, "tts_enabled", bool(enabled))
    path = Path(env_path or find_dotenv(usecwd=True) or ".env")
    line = f"JARVIS_TTS_ENABLED={'true' if enabled else 'false'}"
    try:
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        for i, old in enumerate(lines):
            if old.startswith("JARVIS_TTS_ENABLED="):
                lines[i] = line
                break
        else:
            lines.append(line)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass  # changed for now; the next start reads the old .env
    os.environ["JARVIS_TTS_ENABLED"] = "true" if enabled else "false"
    if enabled:  # warm by the first reply: the cloned-voice server takes up to a minute to start
        import threading

        from voice_app import start_voicebox_if_needed

        threading.Thread(target=start_voicebox_if_needed, daemon=True).start()
    return voice()
