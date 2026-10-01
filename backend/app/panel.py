"""The control panel: a web app served by Jarvis on the home network - for a
phone, the old iPad on the wall, an Android app wrapping it.

The house through the same handlers the voice uses (app/domains/home.py),
so every rule holds here too - gas opens only confirmed, the AC's range.
Every /api call needs the PIN (APP_PIN in .env, set by the resident); no
PIN set - no panel. Five wrong PINs from one address lock it out a minute.
Runs inside Jarvis's own process, on a thread of its own (start_in_thread).
"""

import hmac
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.domains import home
from app.ha_client import HomeAssistantClient, HomeAssistantError
from app.tools.registry import TurnContext

STATIC = Path(__file__).resolve().parent.parent / "static" / "panel"
APK = Path(__file__).resolve().parents[2] / "android" / "build" / "jarvis.apk"  # android/build.py makes it
LOCKOUT_FAILURES, LOCKOUT_SECONDS = 5, 60
CONTROL_FIELDS = ("room", "device", "action", "brightness_pct", "position", "temperature", "confirmed")
WEATHER_SECONDS = 600
SERIES_POINTS = 25  # one an hour over the day - what the room's chart draws
SERIES_KINDS = {"temperature": "temperature", "humidity": "humidity", "co2": "carbon_dioxide"}
NORM_FIELDS = ("room", "temperature", "co2_max", "humidity_min")


def view(rooms: dict[str, list[dict]]) -> list[dict]:
    """app.domains.home's rooms -> what the screen draws: per room its devices
    (one per type - the first, as the voice controls a type per room), its
    readings and norms."""
    out = []
    for name, devices in rooms.items():
        room: dict[str, Any] = {"name": name, "devices": [], "readings": {}, "norms": {}, "dangers": {}, "house": {}}
        seen = set()
        for d in devices:
            kind = d.get("type")
            if kind == "sensor":
                room["readings"][d.get("kind") or "sensor"] = {"value": d.get("value"), "unit": d.get("unit")}
            elif kind == "norm":
                room["norms"][d["kind"]] = {"value": _number(d.get("value")), "unit": d.get("unit"),
                                            "min": d.get("min"), "max": d.get("max")}
            elif kind == "danger":
                room["dangers"]["intrusion" if d["kind"] == "safety" else d["kind"]] = d.get("state") == "on"
            elif kind in ("motion", "window", "door"):
                room["readings"][kind] = {"value": d.get("state") == "on"}
            elif kind == "house_sensor":
                room["house"][d["kind"]] = {"value": d.get("value"), "unit": d.get("unit")}
            elif kind and kind not in seen:
                seen.add(kind)
                state = d.get("state")
                item = {"type": kind, "on": state not in ("off", "closed", "unavailable", None), "state": state}
                for key in ("brightness_pct", "position", "target_temperature", "target_humidity", "action"):
                    if d.get(key) is not None:
                        item[key] = d[key]
                room["devices"].append(item)
        out.append(room)
    return out


def series(points: list[dict], start: datetime, end: datetime, count: int = SERIES_POINTS) -> list[float | None]:
    """A sensor's history -> its value at count evenly spaced moments (the last one known before each)."""
    known = []
    for item in points:
        try:
            known.append((datetime.fromisoformat(item["last_changed"]), float(item["state"])))
        except (KeyError, TypeError, ValueError):
            continue
    known.sort()
    out, i, last = [], 0, None
    for n in range(count):
        at = start + (end - start) * n / (count - 1)
        while i < len(known) and known[i][0] <= at:
            last = known[i][1]
            i += 1
        out.append(last if last is not None else (known[0][1] if known else None))
    return out


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def create_app(client: HomeAssistantClient | None = None, pin: str | None = None, agent_factory=None) -> FastAPI:
    client = client or HomeAssistantClient()
    pin = settings.app_pin if pin is None else pin
    status_h, control_h, norm_h, scenario_h = home.make_handlers(client)
    history_h, schedule_h = home.make_more_handlers(client)
    failures: dict[str, list[float]] = {}
    state: dict[str, Any] = {}

    def check_pin(request: Request) -> None:
        address = request.client.host if request.client else "?"
        now = time.monotonic()
        recent = [t for t in failures.get(address, []) if now - t < LOCKOUT_SECONDS]
        failures[address] = recent
        if len(recent) >= LOCKOUT_FAILURES:
            raise HTTPException(429, "Слишком много неверных PIN - подожди минуту.")
        given = request.headers.get("x-pin", "")
        if not pin or not hmac.compare_digest(given.encode(), pin.encode()):
            recent.append(now)
            raise HTTPException(401, "Неверный PIN.")

    app = FastAPI(title="Jarvis panel", docs_url=None, redoc_url=None, openapi_url=None)
    api = Depends(check_pin)

    async def run(handler, body: dict) -> dict:
        try:
            return await handler(body, TurnContext())
        except HomeAssistantError as exc:
            raise HTTPException(503, f"Дом не отвечает: {exc}") from exc

    @app.get("/")
    async def root():
        return RedirectResponse("/panel/")

    @app.get("/api/hello")
    async def hello():  # no PIN: how the phone app finds Jarvis in the home network - says nothing else
        return {"app": "jarvis"}

    @app.get("/jarvis.apk")
    async def android_app():  # the Android app, to open on the phone and install
        if not APK.exists():
            raise HTTPException(404, "Приложение ещё не собрано: python android/build.py")
        return FileResponse(APK, media_type="application/vnd.android.package-archive", filename="jarvis.apk")

    @app.get("/api/ping", dependencies=[api])
    async def ping():
        return {"ok": True}

    @app.get("/api/house", dependencies=[api])
    async def house():
        try:
            rooms = await home._house(client)
        except HomeAssistantError as exc:
            raise HTTPException(503, f"Дом не отвечает: {exc}") from exc
        return {"rooms": view(rooms)}

    @app.post("/api/control", dependencies=[api])
    async def control(body: dict):
        return await run(control_h, {k: body[k] for k in CONTROL_FIELDS if k in body})

    @app.post("/api/norm", dependencies=[api])
    async def norm(body: dict):
        return await run(norm_h, {k: body[k] for k in NORM_FIELDS if k in body})

    @app.get("/api/scenarios", dependencies=[api])
    async def scenarios():
        return await run(scenario_h, {})

    @app.post("/api/scenarios/run", dependencies=[api])
    async def run_scenario(body: dict):
        return await run(scenario_h, {"name": str(body.get("name") or "")})

    @app.get("/api/schedules", dependencies=[api])
    async def schedules():
        return await run(schedule_h, {"action": "list"})

    @app.post("/api/schedules", dependencies=[api])
    async def change_schedule(body: dict):
        return await run(schedule_h, {k: body[k] for k in ("action", "name", "time") if k in body})

    @app.post("/api/history", dependencies=[api])
    async def history(body: dict):
        return await run(history_h, {k: body[k] for k in ("what", "room", "period", "start", "end") if k in body})

    @app.get("/api/weather", dependencies=[api])
    async def weather():
        """Outside now, for the header - asked once in ten minutes."""
        cached = state.get("weather")
        if cached and time.monotonic() - cached[0] < WEATHER_SECONDS:
            return cached[1]
        from app.domains.weather import make_handler

        result = await make_handler(settings.weather_city)({"days": 1}, TurnContext())
        answer = {"now": result.get("now"), "place": result.get("place")} if "now" in result else {"error": result.get("error")}
        if "now" in answer:
            state["weather"] = (time.monotonic(), answer)
        return answer

    @app.get("/api/series", dependencies=[api])
    async def room_series(room: str, what: str = "temperature"):
        """The room's sensor over the last day, as evenly spaced values for a chart."""
        kind = SERIES_KINDS.get(what)
        if kind is None:
            raise HTTPException(400, "what: temperature, humidity or co2.")
        try:
            rooms = await home._house(client)
            name = home.match_room(room, list(rooms))
            sensor = next((d for d in rooms.get(name, []) if d.get("type") == "sensor" and d.get("kind") == kind), None)
            if sensor is None:
                return {"values": []}
            end = datetime.now().astimezone()
            start = end - timedelta(hours=24)
            points = await client.get_history(sensor["entity_id"], start.isoformat(), end.isoformat())
        except HomeAssistantError as exc:
            raise HTTPException(503, f"Дом не отвечает: {exc}") from exc
        return {"values": series(points, start, end)}

    @app.post("/api/chat", dependencies=[api])
    async def chat(body: dict):
        message = str(body.get("message") or "").strip()
        if not message:
            raise HTTPException(400, "Пустое сообщение.")
        if "agent" not in state:  # built on first use, on this thread - its database connection is its own
            from app.agent import build_default_agent

            state["agent"] = (agent_factory or build_default_agent)()
        result = await state["agent"].chat("panel", "default", message)
        return {"response": result["response"]}

    if STATIC.exists():
        app.mount("/panel", StaticFiles(directory=STATIC, html=True), name="panel")
    return app


def home_address() -> str | None:
    """This computer's address in the home network (192.168.x / 10.x), for the link to show."""
    import socket

    try:  # the address the home router is reached from - a UDP "connect" sends nothing
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.168.0.1", 9))
            routed = probe.getsockname()[0]
        if routed.startswith(("192.168.", "10.")):
            return routed
    except OSError:
        pass
    try:  # no route: the first home-looking address (VirtualBox's 192.168.56.x can be among them)
        addresses = {info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
    except OSError:
        return None
    for prefix in ("192.168.", "10."):
        found = sorted(a for a in addresses if a.startswith(prefix))
        if found:
            return found[0]
    return None


def start_in_thread(port: int | None = None) -> threading.Thread | None:
    """The panel on the home network, on its own thread. None without a PIN."""
    if not settings.app_pin:
        return None
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(create_app(), host="0.0.0.0", port=port or settings.panel_port,
                                           log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True, name="panel")
    thread.start()
    return thread
