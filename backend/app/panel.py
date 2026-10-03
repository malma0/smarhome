"""The control panel: a web app served by Jarvis on the home network - for a
phone, the old iPad on the wall, an Android app wrapping it.

The house through the same handlers the voice uses (app/domains/home.py),
so every rule holds here too - gas opens only confirmed, the AC's range.
Every /api call needs the PIN (APP_PIN in .env, set by the resident); no
PIN set - no panel. Five wrong PINs from one address lock it out a minute.
Runs inside Jarvis's own process, on a thread of its own (start_in_thread).
"""

import asyncio
import hmac
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from app.config import settings
from app.domains import home
from app.ha_client import HomeAssistantClient, HomeAssistantError
from app.tools.registry import TurnContext

STATIC = Path(__file__).resolve().parent.parent / "static" / "panel"
APK = Path(__file__).resolve().parents[2] / "android" / "build" / "jarvis.apk"  # android/build.py makes it
LOCKOUT_FAILURES, LOCKOUT_SECONDS = 5, 60
CONTROL_FIELDS = ("room", "device", "action", "brightness_pct", "position", "temperature", "confirmed")
WEATHER_SECONDS = 600
ENERGY_SECONDS = 60
VOICE_MIN_BYTES = 16000  # half a second of 16 kHz mono 16-bit
VOICE_MAX_BYTES = 16000 * 2 * 60  # a minute
SERIES_POINTS = 25  # one an hour over the day - what the room's chart draws
SERIES_KINDS = {"temperature": "temperature", "humidity": "humidity", "co2": "carbon_dioxide"}
NORM_FIELDS = ("room", "temperature", "co2_max", "humidity_min")
ARM_AUTOMATION = "security_arm_on_leaving"  # virtual_house.py: "Я ушёл" -> armed after ARM_DELAY
ARM_SECONDS = 120  # its ARM_DELAY, for the countdown - when the house has no such setting
ARM_EVENT = "jarvis_arm_soon"  # starts it from the app (virtual_house.py ARM_EVENT)
SETTLE_TRIES, SETTLE_SECONDS = 5, 0.2
RELOAD_TRIES, RELOAD_SECONDS = 15, 0.2  # a script or automation just written shows up within ~3 s
JOURNAL_HOURS = 48
ALL_DAYS = [1, 2, 3, 4, 5, 6, 7]
DAY_DIGITS = {str(d) for d in ALL_DAYS}


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


async def scenes_view(client: HomeAssistantClient) -> list[dict]:
    """The scenarios for their screen: what each does and when it last ran."""
    scenes = []
    for state in await client.get_states():
        if not state["entity_id"].startswith("script."):
            continue
        object_id = state["entity_id"].split(".", 1)[1]
        try:
            config = await client.get_script_config(object_id)
        except HomeAssistantError:
            config = {}
        description = config.get("description") or ""
        attrs = state.get("attributes", {})
        scenes.append({"id": object_id, "name": attrs.get("friendly_name", object_id),
                       "does": home._scenario_does(description), "phrases": home._scenario_phrases(description),
                       "last": attrs.get("last_triggered"),
                       "steps": (config.get("variables") or {}).get("jarvis_steps"),  # None: not made in the app
                       "icon": (config.get("variables") or {}).get("jarvis_icon")})
    return scenes


def schedules_view(states: list[dict], made: dict[str, dict] | None = None) -> list[dict]:
    """The schedules with their time (None: at sunset) and days (Mon=1 .. Sun=7, from input_text.<id>_days);
    made: the app's own schedules' configs by automation id - their time and days are in there."""
    by_id = {s["entity_id"]: s for s in states}
    schedules = []
    for state in states:
        attrs = state.get("attributes", {})
        name = attrs.get("friendly_name", "")
        if not (state["entity_id"].startswith("automation.") and name.startswith(home.SCHEDULE_PREFIX)):
            continue
        own = (made or {}).get(attrs.get("id"))
        if own is not None:
            mine = (own.get("variables") or {}).get("jarvis_schedule") or {}
            schedules.append({"id": attrs["id"], "name": mine.get("name") or name[len(home.SCHEDULE_PREFIX):].strip(),
                              "on": state["state"] == "on", "time": mine.get("time"), "days": mine.get("days") or ALL_DAYS,
                              "days_editable": True, "app": True, "scene": mine.get("scene")})
            continue
        slug = state["entity_id"].split(".", 1)[1]
        days_helper = by_id.get(f"input_text.{slug}_days")
        days = ALL_DAYS
        if days_helper:
            days = sorted({int(d) for d in str(days_helper["state"]).split(",") if d.strip() in DAY_DIGITS})
        schedules.append({"id": slug, "name": name[len(home.SCHEDULE_PREFIX):].strip(), "on": state["state"] == "on",
                          "time": (by_id.get(f"input_datetime.{slug}_time", {}).get("state") or "")[:5] or None,
                          "days": days, "days_editable": days_helper is not None})
    return schedules


async def security_view(client: HomeAssistantClient, now: datetime | None = None) -> dict:
    """The guard (on / arming / off, since when) and the journal with what the house did about alarms."""
    from app import journal

    now = now or datetime.now().astimezone()
    states = await client.get_states()
    guard = next((s for s in states if s["entity_id"] == journal.GUARD), None)
    arming = next((s for s in states if s["entity_id"].startswith("automation.")
                   and s.get("attributes", {}).get("id") == ARM_AUTOMATION), None)
    follow = journal.watched(states)
    start = now - timedelta(hours=JOURNAL_HOURS)
    histories = await client.get_histories(sorted(follow), start.isoformat(), now.isoformat())
    lines = journal.events(follow, histories)
    for line in [x for x in lines if x["k"] in ("guard", "alarm")][:10]:  # the logbook only around these
        at = datetime.fromisoformat(line["at"])
        if line["k"] == "guard":
            book = await client.get_logbook((at - timedelta(seconds=2)).isoformat(), (at + timedelta(seconds=2)).isoformat())
            line["detail"] = journal.why(line, book)
        else:
            book = await client.get_logbook(at.isoformat(), (at + journal.ACTS_WINDOW).isoformat())
            line["acts"] = journal.acts(line, book)
    attrs = (arming or {}).get("attributes", {})
    return {
        "armed": bool(guard and guard["state"] == "on"),
        # HA's last_changed restarts with HA; the journal's own last switch is the real "since"
        "since": next((x["at"] for x in lines if x["k"] == "guard"), guard.get("last_changed") if guard else None),
        "arming": bool(attrs.get("current")) and not (guard and guard["state"] == "on"),
        "arming_since": attrs.get("last_triggered"),
        "arm_seconds": next((int(float(s["state"])) * 60 for s in states  # the delay the resident set
                             if s["entity_id"] == "input_number.security_arm_delay_minutes"
                             and s["state"] not in ("unknown", "unavailable")), ARM_SECONDS),
        "can_delay": arming is not None,
        "journal": [{k: v for k, v in line.items() if k != "entity_id"} for line in lines],
    }


DEVICE_WORDS = {"light": ("light", "Свет"), "curtains": ("curtain", "Шторы"), "socket": ("socket", "Розетка"),
                "ac": ("ac", "Кондиционер"), "heating": ("heat", "Отопление"), "ventilation": ("vent", "Вентиляция"),
                "humidifier": ("humid", "Увлажнитель"), "water_valve": ("water", "Кран воды"),
                "gas_valve": ("gas", "Кран газа"), "security": ("shield", "Охрана"), "kettle": ("kettle", "Чайник")}
NORM_WORDS = {"temperature": ("thermo", "°"), "co2_max": ("co2", " ppm"), "humidity_min": ("humid", "%")}


def acts_of(actions: list[dict]) -> list[dict]:
    """What the house did this turn, as the chat's badges: [{"icon": "light", "t": "Свет · Кухня — выкл"}]."""
    acts = []
    for action in actions:
        result = action.get("result") or {}
        tool = action.get("tool")
        if tool == "control_devices":
            for done in result.get("done", []):
                ic, word = DEVICE_WORDS.get(done.get("device"), ("power", str(done.get("device"))))
                state = "вкл" if done.get("action") == "on" else "выкл"
                if done.get("device") == "curtains":
                    state = "открыть" if done.get("action") == "on" else "закрыть"
                acts.append({"icon": ic, "t": f"{word} · {done.get('room')} — {state}"})
        elif tool == "set_room_norm":
            for done in result.get("done", []):
                for kind, (ic, unit) in NORM_WORDS.items():
                    if kind in done:
                        value = f"{done[kind]:g}".replace(".", ",")
                        acts.append({"icon": ic, "t": f"Норма · {done.get('room')} — {value}{unit}"})
        elif tool == "run_scenario" and result.get("ran"):
            acts.append({"icon": "scenes", "t": f"Сценарий «{result['ran']}»"})
        elif tool == "house_schedule" and result.get("done"):
            done = result["done"]
            acts.append({"icon": "clock", "t": f"Расписание «{done.get('name')}» — "
                                               + ("вкл" if done.get("on") else "выкл")})
    return acts


async def whisper(wav: bytes, resident_ids: list[str]) -> str:
    """The phone's recording -> text, the same way as the laptop's microphone (voice_app):
    Whisper with the house's words and the residents' names, noise thrown away."""
    from app.transcript_filter import is_hallucination
    from voice_app import build_whisper_prompt, transcribe

    prompt = build_whisper_prompt(settings.whisper_vocabulary, resident_ids)
    text = await transcribe(wav, settings.groq_api_key, settings.groq_base_url, prompt=prompt)
    return "" if is_hallucination(text) else text


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


def create_app(client: HomeAssistantClient | None = None, pin: str | None = None, agent_factory=None,
               transcriber=None) -> FastAPI:
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
    # the page, its scripts and styles go about five times smaller - an old phone on Wi-Fi opens it sooner
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    api = Depends(check_pin)

    @app.middleware("http")
    async def always_fresh(request: Request, call_next):
        # the phone's WebView would otherwise keep an old app.js / app.css for hours after an update;
        # no-cache still lets it reuse the file when the ETag says it's the same
        response = await call_next(request)
        if request.url.path.startswith("/panel/fonts/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"  # fonts never change
        elif request.url.path.startswith("/panel"):
            response.headers["Cache-Control"] = "no-cache"
        return response

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

    @app.get("/api/scenes", dependencies=[api])
    async def scenes():
        try:
            return {"scenes": await scenes_view(client)}
        except HomeAssistantError as exc:
            raise HTTPException(503, f"Дом не отвечает: {exc}") from exc

    @app.post("/api/scenes", dependencies=[api])
    async def save_scene(body: dict):
        """{name, phrases, steps, id?}: a scenario made (or changed) in the app - a Home Assistant script."""
        from app import scenes as made

        try:
            current = await scenes_view(client)
            config, _ = await made.build(client, body.get("name"), body.get("phrases") or [], body.get("steps") or [],
                                         str(body.get("icon") or "play"))
            if body.get("id"):
                scene_id = str(body["id"])
                if not any(s["id"] == scene_id for s in current):
                    return {"error": "Нет такого сценария."}
            else:
                if any(s["name"].casefold() == config["alias"].casefold() for s in current):
                    return {"error": "Сценарий с таким названием уже есть."}
                scene_id = made.object_id(config["alias"], {s["id"] for s in current})
            await client.save_script_config(scene_id, config)
            for _ in range(RELOAD_TRIES):  # Home Assistant reloads its scripts a moment after
                listed = await scenes_view(client)
                mine = next((x for x in listed if x["id"] == scene_id), None)
                if mine and mine["steps"] == config["variables"]["jarvis_steps"]:
                    break
                await asyncio.sleep(RELOAD_SECONDS)
            return {"scenes": listed, "id": scene_id}
        except (ValueError, TypeError) as exc:
            return {"error": f"Не подходит: {exc}"}
        except HomeAssistantError as exc:
            raise HTTPException(503, f"Дом не отвечает: {exc}") from exc

    @app.post("/api/scenes/try", dependencies=[api])
    async def try_scene(body: dict):
        """The editor's "Проверить": the steps run now, one after another, nothing saved."""
        from app import scenes as made

        try:
            config, _ = await made.build(client, "Проверка", [], body.get("steps") or [])
            for action in config["sequence"]:
                domain, service = action["action"].split(".", 1)
                await client.call_service(domain, service, action["target"]["entity_id"], action.get("data"))
            return {"done": len(config["sequence"])}
        except (ValueError, TypeError) as exc:
            return {"error": f"Не подходит: {exc}"}
        except HomeAssistantError as exc:
            raise HTTPException(503, f"Дом не отвечает: {exc}") from exc

    @app.delete("/api/scenes/{scene_id}", dependencies=[api])
    async def delete_scene(scene_id: str):
        try:
            if not any(s["id"] == scene_id for s in await scenes_view(client)):
                return {"error": "Нет такого сценария."}
            await client.delete_script_config(scene_id)
            for schedule in (await all_schedules())["schedules"]:
                if schedule.get("app") and schedule.get("scene") == scene_id and schedule["on"]:
                    entity = next((s["entity_id"] for s in await client.get_states()
                                   if s.get("attributes", {}).get("id") == schedule["id"]), None)
                    if entity:  # it would only fail now: off, as the delete dialog says
                        await client.call_service("automation", "turn_off", entity)
            return {"scenes": await scenes_view(client)}
        except HomeAssistantError as exc:
            raise HTTPException(503, f"Дом не отвечает: {exc}") from exc

    async def all_schedules() -> dict:
        from app.scenes import SCHEDULE_PREFIX

        try:
            states = await client.get_states()
            made = {}
            for s in states:
                automation_id = s.get("attributes", {}).get("id") or ""
                if s["entity_id"].startswith("automation.") and automation_id.startswith(SCHEDULE_PREFIX):
                    made[automation_id] = await client.get_automation_config(automation_id)
            return {"schedules": schedules_view(states, made)}
        except HomeAssistantError as exc:
            raise HTTPException(503, f"Дом не отвечает: {exc}") from exc

    async def rewrite_schedule(found: dict, **change) -> dict:
        """The app's own schedule with a new time or days: its automation written again."""
        from app.scenes import schedule_config

        mine = {"name": found["name"], "time": found["time"], "days": found["days"], "scene": found["scene"], **change}
        try:
            config = schedule_config(mine["name"], mine["time"], mine["days"], mine["scene"])
        except ValueError as exc:
            return {"error": f"Не подходит: {exc}"}
        try:
            await client.save_automation_config(found["id"], config)
        except HomeAssistantError as exc:
            raise HTTPException(503, f"Дом не отвечает: {exc}") from exc
        return await all_schedules()

    @app.get("/api/schedules", dependencies=[api])
    async def schedules():
        return await all_schedules()

    @app.post("/api/schedules", dependencies=[api])
    async def change_schedule(body: dict):
        """enable / disable / set_time go through the tool's own rules; set_days, create and delete are the app's."""
        action = body.get("action")
        if action == "create":
            from app.scenes import SCHEDULE_PREFIX, object_id, schedule_config

            current = (await all_schedules())["schedules"]
            try:
                config = schedule_config(body.get("name"), body.get("time"), body.get("days"), body.get("scene"))
            except ValueError as exc:
                return {"error": f"Не подходит: {exc}"}
            new_id = SCHEDULE_PREFIX + object_id(str(body.get("name")), {s["id"][len(SCHEDULE_PREFIX):] for s in current
                                                                         if s.get("app")})
            try:
                await client.save_automation_config(new_id, config)
            except HomeAssistantError as exc:
                raise HTTPException(503, f"Дом не отвечает: {exc}") from exc
            for _ in range(RELOAD_TRIES):  # and its automations
                listed = await all_schedules()
                if any(x["id"] == new_id for x in listed["schedules"]):
                    break
                await asyncio.sleep(RELOAD_SECONDS)
            return listed
        found = next((s for s in (await all_schedules())["schedules"] if s["id"] == body.get("id")), None)
        if found is not None and found.get("app"):
            if action == "delete":
                try:
                    await client.delete_automation_config(found["id"])
                except HomeAssistantError as exc:
                    raise HTTPException(503, f"Дом не отвечает: {exc}") from exc
                return await all_schedules()
            if action == "set_time":
                return await rewrite_schedule(found, time=body.get("time"))
            if action == "set_days":
                return await rewrite_schedule(found, days=body.get("days"))
            if action == "update":
                return await rewrite_schedule(found, **{k: body[k] for k in ("name", "time", "days", "scene") if k in body})
        if action == "delete":
            return {"error": "Удалить можно только расписание, созданное в приложении."}
        if action == "set_days":
            days = sorted({int(d) for d in body.get("days") or [] if str(d) in DAY_DIGITS})
            if not days:
                return {"error": "Нужен хотя бы один день - чтобы не срабатывало, выключи расписание."}
            if found is None or not found["days_editable"]:
                return {"error": "Нет такого расписания с днями."}
            try:
                await client.call_service("input_text", "set_value", f"input_text.{found['id']}_days",
                                          {"value": ",".join(str(d) for d in days)})
            except HomeAssistantError as exc:
                raise HTTPException(503, f"Дом не отвечает: {exc}") from exc
            return await all_schedules()
        result = await run(schedule_h, {k: body[k] for k in ("action", "name", "time") if k in body})
        return result if "error" in result else await all_schedules()

    @app.get("/api/energy", dependencies=[api])
    async def energy(period: str = "day"):
        """kWh by hour / day for the Energy screen - asked at most once a minute per period."""
        from app.energy import PERIODS, energy_view

        if period not in PERIODS:
            raise HTTPException(400, "period: day, week или month.")
        cached = state.get(("energy", period))
        if cached and time.monotonic() - cached[0] < ENERGY_SECONDS:
            return cached[1]
        try:
            view = await energy_view(client, period, datetime.now().astimezone())
        except HomeAssistantError as exc:
            raise HTTPException(503, f"Дом не отвечает: {exc}") from exc
        state[("energy", period)] = (time.monotonic(), view)
        return view

    @app.get("/api/layout", dependencies=[api])
    async def get_layout():
        from app import layout

        return {"layout": layout.load()}

    @app.put("/api/layout", dependencies=[api])
    async def put_layout(body: dict):
        """The plan from the layout editor; null resets to the app's own sketch."""
        from app import layout

        if body.get("layout") is None:
            layout.FILE.unlink(missing_ok=True)
            return {"layout": None}
        try:
            return {"layout": layout.save(body["layout"])}
        except (ValueError, TypeError, AttributeError) as exc:
            return {"error": f"План не сохранён: {exc}"}

    @app.get("/api/alerts", dependencies=[api])
    async def alerts():
        """The dangers going on now - the alarm screen, and the phone app's watcher every few seconds."""
        from app import alerts as danger_now

        try:
            return {"alerts": await danger_now.active(client)}
        except HomeAssistantError as exc:
            raise HTTPException(503, f"Дом не отвечает: {exc}") from exc

    @app.get("/api/automation", dependencies=[api])
    async def automation():
        from app import house_settings

        try:
            return house_settings.view(await client.get_states())
        except HomeAssistantError as exc:
            raise HTTPException(503, f"Дом не отвечает: {exc}") from exc

    @app.post("/api/automation", dependencies=[api])
    async def change_automation(body: dict):
        """{key, value}: one knob of the house's behaviour, or a norm for every room."""
        from app import house_settings

        try:
            states = await client.get_states()
            try:
                calls = house_settings.changes(str(body.get("key")), body.get("value"), states)
            except (ValueError, TypeError) as exc:
                return {"error": f"Не подходит: {exc}"}
            for domain, service, entity_id, data in calls:
                await client.call_service(domain, service, entity_id, data or None)
            return house_settings.view(await client.get_states())
        except HomeAssistantError as exc:
            raise HTTPException(503, f"Дом не отвечает: {exc}") from exc

    @app.get("/api/security", dependencies=[api])
    async def security():
        try:
            return await security_view(client)
        except HomeAssistantError as exc:
            raise HTTPException(503, f"Дом не отвечает: {exc}") from exc

    @app.post("/api/security", dependencies=[api])
    async def change_security(body: dict):
        """arm: in ARM_SECONDS, time to walk out (the "Я ушёл" automation's own wait); arm_now; disarm; cancel."""
        action = body.get("action")
        try:
            states = await client.get_states()
            arming = next((s["entity_id"] for s in states if s["entity_id"].startswith("automation.")
                           and s.get("attributes", {}).get("id") == ARM_AUTOMATION), None)
            if action == "arm" and arming:
                await client.fire_event(ARM_EVENT)
            elif action in ("arm", "arm_now"):
                await client.call_service("input_boolean", "turn_on", "input_boolean.security_armed")
            elif action in ("disarm", "cancel"):
                if arming:  # stops a countdown still running, then lets it work again
                    await client.call_service("automation", "turn_off", arming)
                    await client.call_service("automation", "turn_on", arming)
                await client.call_service("input_boolean", "turn_off", "input_boolean.security_armed")
            else:
                return {"error": "action: arm, arm_now, disarm или cancel."}
            # the automation's "current" shows up a moment after the call - wait for it (a second at most)
            want_on = action in ("arm", "arm_now")
            for _ in range(SETTLE_TRIES):
                view = await security_view(client)
                if (view["armed"] or view["arming"]) == want_on:
                    break
                await asyncio.sleep(SETTLE_SECONDS)
            return view
        except HomeAssistantError as exc:
            raise HTTPException(503, f"Дом не отвечает: {exc}") from exc

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
        return await answer(message)

    def agent():
        if "agent" not in state:  # built on first use, on this thread - its database connection is its own
            from app.agent import build_default_agent

            state["agent"] = (agent_factory or build_default_agent)()
        return state["agent"]

    async def answer(message: str, heard: bool = False) -> dict:
        result = await agent().chat("panel", "default", message)
        reply = {"response": result["response"], "acts": acts_of(result.get("actions", [])),
                 "local": bool(result.get("local"))}
        if heard:
            reply["heard"] = message
        return reply

    @app.post("/api/voice", dependencies=[api])
    async def voice(body: dict):
        """{"audio": base64 WAV} from the phone app's microphone -> what was heard and Jarvis's answer."""
        import base64
        import binascii

        try:
            wav = base64.b64decode(str(body.get("audio") or ""), validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(400, "Запись не читается.") from None
        if len(wav) < VOICE_MIN_BYTES:
            return {"error": "Слишком коротко - не расслышал."}
        if len(wav) > VOICE_MAX_BYTES:
            return {"error": "Слишком длинная запись."}
        try:
            residents = agent().memory.list_resident_ids()
            text = await (transcriber or whisper)(wav, residents)
        except Exception as exc:  # noqa: BLE001 - the network or the key: said, not crashed
            return {"error": f"Не получилось распознать: {exc}"}
        if not text:
            return {"error": "Не расслышал - скажи ещё раз."}
        return await answer(text, heard=True)

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
