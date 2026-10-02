"""The flat's plan as the resident drew it in the app's layout editor.

Kept in backend/layout.json, next to Jarvis's database (gitignored - it's this
home's own). Coordinates are plan units, 50 to a metre. Rooms are keyed by their
Home Assistant name - the plan draws the house's rooms, it doesn't make new ones.

    {"rooms": {"Кухня": {"x": 210, "y": 0, "w": 140, "h": 200}, ...},
     "devices": {"Кухня|light": [280, 112], ...},
     "doors": [["h", 200, 118, 152], ...],            # along y=200 from x=118 to 152
     "windows": [["v", 0, 375, 465, "Спальня"], ...],
     "front": {"room": "Прихожая", "x": 310, "y": 245}}
"""

from __future__ import annotations

import json
from pathlib import Path

FILE = Path(__file__).resolve().parent.parent / "layout.json"
LIMIT = 2000  # plan units: 40 m - nobody's flat is wider


def _num(v) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not -LIMIT <= v <= LIMIT:
        raise ValueError("a coordinate out of the plan")
    return round(float(v), 1)


def _segment(seg, with_room: bool) -> list:
    if not isinstance(seg, list) or len(seg) != (5 if with_room else 4) or seg[0] not in ("h", "v"):
        raise ValueError("a door or window is [h|v, along, from, to(, room)]")
    out = [seg[0], _num(seg[1]), _num(seg[2]), _num(seg[3])]
    if with_room:
        out.append(str(seg[4])[:60])
    return out


def clean(layout: dict) -> dict:
    """Only what the plan can draw, every number checked; ValueError for anything else."""
    if not isinstance(layout, dict) or not isinstance(layout.get("rooms"), dict) or not layout["rooms"]:
        raise ValueError("a plan needs rooms")
    rooms = {}
    for name, r in layout["rooms"].items():
        if not isinstance(r, dict):
            raise ValueError("a room is {x, y, w, h}")
        rect = {k: _num(r.get(k)) for k in ("x", "y", "w", "h")}
        if rect["w"] < 20 or rect["h"] < 20:
            raise ValueError("a room that small isn't a room")
        rooms[str(name)[:60]] = rect
    devices = {}
    for key, xy in (layout.get("devices") or {}).items():
        if not isinstance(xy, list) or len(xy) != 2 or "|" not in str(key):
            raise ValueError("a device is 'room|type': [x, y]")
        devices[str(key)[:80]] = [_num(xy[0]), _num(xy[1])]
    out = {"rooms": rooms, "devices": devices,
           "doors": [_segment(d, False) for d in layout.get("doors") or []],
           "windows": [_segment(w, True) for w in layout.get("windows") or []]}
    front = layout.get("front")
    if front:
        out["front"] = {"room": str(front.get("room", ""))[:60], "x": _num(front.get("x")), "y": _num(front.get("y"))}
    return out


def load() -> dict | None:
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None  # never drawn: the app shows its own sketch


def save(layout: dict) -> dict:
    good = clean(layout)
    FILE.write_text(json.dumps(good, ensure_ascii=False, indent=1), encoding="utf-8")
    return good
