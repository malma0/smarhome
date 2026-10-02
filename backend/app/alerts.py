"""The dangers going on right now, for the phone: its alarm screen and its notifications.

Read from the danger sensors' state (app.domains.home DANGER_CLASSES) - a danger
lasts as long as its sensor says so. What the house already did about it comes from
the logbook of the minutes after it began (app.journal.acts).
"""

from __future__ import annotations

from datetime import datetime

from app import journal
from app.domains.home import DANGER_CLASSES
from app.words import locative

TITLES = {"moisture": "Протечка", "gas": "Утечка газа", "carbon_monoxide": "Угарный газ", "smoke": "Дым",
          "safety": "Движение"}
ADVICE = {
    "moisture": "Вытри воду и найди, откуда течёт. Кран воды открой, когда датчик высохнет.",
    "gas": "Открой окна, не включай свет и огонь. Если пахнет газом — уходи и звони 112.",
    "carbon_monoxide": "Открой окна и выйди на воздух. Если кружится голова — звони 112.",
    "smoke": "Проверь, что горит. Если это пожар — уходи и звони 112.",
    "safety": "Дом под охраной, а внутри движение. Если это ты — сними охрану.",
}


async def active(client) -> list[dict]:
    """[{key, kind, room, title, where, advice, icon, since, acts}] - every danger sensor that is on."""
    states = await client.get_states()
    found = []
    for state in states:
        attrs = state.get("attributes", {})
        kind = attrs.get("device_class")
        if not (state["entity_id"].startswith("binary_sensor.") and kind in DANGER_CLASSES and state["state"] == "on"):
            continue
        room = journal.room_of(attrs.get("friendly_name", state["entity_id"]))
        found.append({"key": state["entity_id"], "kind": kind, "room": room, "title": TITLES.get(kind, "Опасность"),
                      "where": locative(room), "advice": ADVICE.get(kind, ""),
                      "icon": journal.ALARM_ICONS.get(kind, "alert"), "since": state.get("last_changed")})
    for alert in found:  # what the house did in the minutes after it began
        alert["acts"] = []
        if alert["since"]:
            start = datetime.fromisoformat(alert["since"])
            book = await client.get_logbook(start.isoformat(), (start + journal.ACTS_WINDOW).isoformat())
            alert["acts"] = journal.acts({"at": alert["since"]}, book)
    return found
