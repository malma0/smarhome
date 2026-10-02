"""The house's own behaviour, as the app's "Автоматика и нормы" screen shows and changes it.

The knobs are Home Assistant helpers (homeassistant/virtual_house.py SETTINGS): how
long a light stays on in an empty room, the night light, the guard's delays. The
norms "for all rooms" set every room's own norm helper to one value - a room's norm
is what the house keeps, there is no separate default.
"""

from __future__ import annotations

NUMBERS = {  # key: (entity, min, max)
    "light_off_minutes": ("input_number.light_auto_off_minutes", 1, 60),
    "night_pct": ("input_number.night_light_pct", 5, 100),
    "arm_delay_minutes": ("input_number.security_arm_delay_minutes", 0, 10),
    "entry_delay_minutes": ("input_number.security_entry_delay_minutes", 1, 5),
}
NIGHT_LIGHT = "input_boolean.night_light"
TIMES = {"night_start": "input_datetime.night_light_start", "night_end": "input_datetime.night_light_end"}
NIGHT_ROOMS = "input_text.night_light_rooms"
NORMS = {"temperature": "_temperature_norm", "co2_max": "_co2_max", "humidity_min": "_humidity_min"}


def _number(state: dict | None) -> float | None:
    try:
        return float(state["state"])
    except (TypeError, KeyError, ValueError):
        return None


def view(states: list[dict]) -> dict:
    by_id = {s["entity_id"]: s for s in states}
    out: dict = {key: _number(by_id.get(entity)) for key, (entity, _, _) in NUMBERS.items()}
    out = {k: (int(v) if v is not None else None) for k, v in out.items()}
    out["night_light"] = by_id.get(NIGHT_LIGHT, {}).get("state") == "on"
    for key, entity in TIMES.items():
        out[key] = (by_id.get(entity, {}).get("state") or "")[:5] or None
    out["night_rooms"] = [r for r in (by_id.get(NIGHT_ROOMS, {}).get("state") or "").split(",") if r]
    # the rooms, by their motion sensors: slug -> "Спальня" (from "Спальня: движение")
    out["rooms"] = sorted(
        ({"slug": s["entity_id"][len("binary_sensor."):-len("_motion")],
          "name": s.get("attributes", {}).get("friendly_name", "").split(":")[0].strip()}
         for s in states if s["entity_id"].startswith("binary_sensor.") and s["entity_id"].endswith("_motion")),
        key=lambda r: r["name"])
    norms = {}
    for kind, suffix in NORMS.items():
        found = [s for s in states if s["entity_id"].startswith("input_number.") and s["entity_id"].endswith(suffix)]
        values = sorted({_number(s) for s in found if _number(s) is not None})
        if not found:
            continue
        attrs = found[0].get("attributes", {})
        norms[kind] = {"value": values[len(values) // 2] if values else None, "same": len(values) == 1,
                       "min": attrs.get("min"), "max": attrs.get("max"), "rooms": len(found)}
    out["norms"] = norms
    return out


def changes(key: str, value, states: list[dict]) -> list[tuple[str, str, str, dict]]:
    """What to call for one change: [(domain, service, entity_id, data)]. ValueError for nonsense."""
    if key in NUMBERS:
        entity, low, high = NUMBERS[key]
        number = int(round(float(value)))
        if not low <= number <= high:
            raise ValueError(f"от {low} до {high}")
        return [("input_number", "set_value", entity, {"value": number})]
    if key == "night_light":
        return [("input_boolean", "turn_on" if value else "turn_off", NIGHT_LIGHT, {})]
    if key in TIMES:
        hours, minutes = (int(x) for x in str(value).split(":")[:2])
        if not (0 <= hours < 24 and 0 <= minutes < 60):
            raise ValueError("время ЧЧ:ММ")
        return [("input_datetime", "set_datetime", TIMES[key], {"time": f"{hours:02d}:{minutes:02d}:00"})]
    if key == "night_rooms":
        known = {r["slug"] for r in view(states)["rooms"]}
        rooms = [r for r in value if r in known]
        return [("input_text", "set_value", NIGHT_ROOMS, {"value": ",".join(rooms)})]
    if key.startswith("norm_") and key[5:] in NORMS:
        suffix = NORMS[key[5:]]
        calls = []
        for s in states:
            if s["entity_id"].startswith("input_number.") and s["entity_id"].endswith(suffix):
                attrs = s.get("attributes", {})
                number = min(max(float(value), attrs.get("min", float("-inf"))), attrs.get("max", float("inf")))
                calls.append(("input_number", "set_value", s["entity_id"], {"value": number}))
        return calls
    raise ValueError("нет такого параметра")
