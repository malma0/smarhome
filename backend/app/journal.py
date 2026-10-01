"""The security journal for the panel: what the sensors and the guard did, newest first.

Built from the history of a few entities (movement, windows, doors, the danger
sensors, input_boolean.security_armed), not from Home Assistant's logbook - that
one is mostly the virtual house's own physics ticking every minute. The logbook
is only asked about the minute after an alarm: what the house did about it.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.danger import DANGER_WORDS
from app.domains.home import DANGER_CLASSES

GUARD = "input_boolean.security_armed"
OPENINGS = ("window", "door")
MOTION_GAP = timedelta(minutes=15)  # movement in a room again within this: the same visit, not a new line
ACTS_WINDOW = timedelta(minutes=2)
REACTIONS = ("Опасность", "Охрана:")  # aliases of the automations that answer an alarm (virtual_house.py)
ALARM_ICONS = {"moisture": "leak", "gas": "gas", "carbon_monoxide": "gas", "smoke": "smoke", "safety": "motion"}
STATE_WORDS = {"on": "вкл", "off": "выкл", "open": "открыт", "closed": "закрыт"}
LIMIT = 60


def watched(states: list[dict]) -> dict[str, tuple[str, str]]:
    """{entity_id: (kind, room)} of what the journal follows; kind: motion / window / door / a danger class / guard."""
    found = {}
    for state in states:
        entity_id = state["entity_id"]
        attrs = state.get("attributes", {})
        kind = attrs.get("device_class")
        if entity_id.startswith("binary_sensor.") and (kind in ("motion", *OPENINGS) or kind in DANGER_CLASSES):
            found[entity_id] = (kind, room_of(attrs.get("friendly_name", entity_id)))
        elif entity_id == GUARD:
            found[entity_id] = ("guard", "")
    return found


def room_of(name: str) -> str:
    """'Спальня: движение' -> 'Спальня'."""
    return name.split(":", 1)[0].strip() if ":" in name else name


def _line(kind: str, room: str, on: bool) -> dict | None:
    if kind == "motion":
        return {"k": "sensor", "icon": "motion", "title": f"Движение: {room}"} if on else None
    if kind == "window":
        return {"k": "sensor", "icon": "window", "title": f"{'Открыто' if on else 'Закрыто'} окно: {room}"}
    if kind == "door":
        return {"k": "sensor", "icon": "door", "title": f"{'Открыта' if on else 'Закрыта'} дверь: {room}"}
    if kind == "guard":
        return {"k": "guard", "icon": "shieldok" if on else "home", "title": "Охрана включена" if on else "Охрана снята"}
    if on:
        return {"k": "alarm", "icon": ALARM_ICONS.get(kind, "alert"), "title": f"Тревога: {DANGER_WORDS.get(kind, kind)} — {room}"}
    return None  # the all-clear goes onto the alarm's own line


def events(follow: dict[str, tuple[str, str]], histories: dict[str, list[dict]]) -> list[dict]:
    """Every change of the followed entities as a journal line: {k, icon, title, at, ...}, newest first."""
    lines = []
    for entity_id, series in histories.items():
        if entity_id not in follow:
            continue
        kind, room = follow[entity_id]
        before, last_motion, open_alarm = None, None, None
        for item in series:
            value = item.get("state")
            if value not in ("on", "off"):
                continue  # unknown / unavailable
            at = datetime.fromisoformat(item["last_changed"])
            if before is None or value == before:
                before = value  # the first record is the state the period began with, not a change
                continue
            before = value
            if kind == "motion" and value == "on":
                if last_motion and at - last_motion < MOTION_GAP:
                    last_motion = at
                    continue
                last_motion = at
            if kind in DANGER_CLASSES and value == "off":
                if open_alarm is not None:
                    open_alarm["cleared"] = at.isoformat()
                    open_alarm = None
                continue
            line = _line(kind, room, value == "on")
            if line is None:
                continue
            line.update({"at": at.isoformat(), "entity_id": entity_id})
            if line["k"] == "alarm":
                open_alarm = line
            lines.append(line)
    lines.sort(key=lambda e: e["at"], reverse=True)
    return lines[:LIMIT]


def why(line: dict, logbook: list[dict]) -> str:
    """The guard's line: what switched it ('Сценарий «Я ушёл»...'), from the logbook entry of that change."""
    for entry in logbook:
        if entry.get("entity_id") == line["entity_id"] and entry.get("when", "")[:19] == line["at"][:19]:
            return entry.get("context_name") or ""
    return ""


def acts(line: dict, logbook: list[dict]) -> list[dict]:
    """What the house did in the minutes after an alarm: [{"t": "Кухня: кран воды — выкл", "at": "+2 с"}]."""
    start = datetime.fromisoformat(line["at"])
    done = []
    for entry in logbook:
        if not str(entry.get("context_name", "")).startswith(REACTIONS) or "state" not in entry:
            continue
        when = datetime.fromisoformat(entry["when"])
        if not start <= when <= start + ACTS_WINDOW or entry.get("entity_id", "").startswith("input_boolean."):
            continue
        seconds = int((when - start).total_seconds())
        done.append({"t": f"{entry.get('name', entry.get('entity_id'))} — {STATE_WORDS.get(entry['state'], entry['state'])}",
                     "at": f"+{seconds} с" if seconds < 60 else f"+{seconds // 60} мин"})
    return done
