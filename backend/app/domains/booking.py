"""Online booking - "запиши меня в парикмахерскую President к Тарасу на 3".

First step (this file): find the place on the web and tell how it takes
bookings - online (through which system, the page) or only by phone - and
open its booking page. Booking by voice itself needs the booking system's
API (YCLIENTS - the most common for barbers and salons) and comes next.
"""

import json
import re
import webbrowser
from datetime import datetime
from urllib.parse import urlparse

from app.config import settings
from app.domains.web_answer import browse
from app.tools.registry import Tool, ToolRegistry, TurnContext

SYSTEMS = ("yclients", "dikidi", "altegio", "yandex", "own_site", "other", "none")


def _instructions(now: datetime) -> str:
    return (
        f"Today is {now:%Y-%m-%d}. The resident lives in {settings.weather_city or 'Russia'}. Find the business "
        "they mean (Yandex Maps, 2GIS, its own site) and how it takes bookings. Reply with ONLY a JSON object: "
        '{"name": "...", "address": "...", "phone": "...", "online_booking": true|false, '
        '"system": "yclients"|"dikidi"|"altegio"|"yandex"|"own_site"|"other"|"none", '
        '"booking_url": "the direct online booking page" or null}. '
        "Never invent a URL or a phone - null when not found."
    )


def parse(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        found = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(found, dict) or not found.get("name"):
        return None
    url = found.get("booking_url")
    if url and urlparse(str(url)).scheme not in ("http", "https"):
        found["booking_url"] = None
    if found.get("system") not in SYSTEMS:
        found["system"] = "other" if found.get("booking_url") else "none"
    found["online_booking"] = bool(found.get("online_booking") and found.get("booking_url"))
    return {k: found.get(k) for k in ("name", "address", "phone", "online_booking", "system", "booking_url")}


def make_handler(post=None, open_url=webbrowser.open, now=lambda: datetime.now().astimezone()):
    last: dict = {}

    async def booking(tool_input: dict, ctx: TurnContext) -> dict:
        action = tool_input.get("action") or "find"
        if action == "find":
            place = (tool_input.get("place") or "").strip()
            if not place:
                return {"error": "Which place?"}
            found = await browse(_instructions(now()), place, post, max_tokens=500)
            if "error" in found:
                return found
            place_info = parse(found["text"])
            if place_info is None:
                return {"error": f"Couldn't find {place!r} for sure."}
            last.clear()
            last.update(place_info)
            # Live, the model told "Записала вас к Тарасу на 15:00" after only this lookup.
            return {**place_info, "booked": False,
                    "note": "Nothing is booked - this only found the place. Don't say it's booked."}
        if action == "open":
            url = last.get("booking_url")
            if not url:
                return {"error": "No booking page found - find the place first, or it has no online booking."}
            open_url(url)
            return {"opened": url, "name": last.get("name")}
        if action == "book":
            return {"error": "Booking by voice isn't connected yet - offer to open the booking page instead "
                             "(action open), where the resident picks the time."}
        return {"error": "action must be find, open or book."}

    return booking


def register(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            name="booking",
            description=(
                "Booking a place: barber, salon, clinic, restaurant... find: the place as the resident named it "
                "('парикмахерская President', with the city if said) - tells whether it takes online bookings, "
                "through which system, its phone and address; without online booking say so and read out its phone number (you can't call anyone). "
                "open: opens the found place's booking page in the browser. book: booking by voice (not "
                "connected yet). Takes ~10 s to find. Never tell the resident they are booked unless a result "
                "says booked: true."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["find", "open", "book"]},
                    "place": {"type": ["string", "null"]},
                },
                "required": ["action"],
            },
            handler=make_handler(),
        )
    )
