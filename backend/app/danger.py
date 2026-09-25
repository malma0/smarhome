"""Dangers - the one thing Jarvis speaks up about unasked.

The resident's rule (docs/TZ.md §5): Jarvis never tells anyone a room is
stuffy or hot - what it can fix, it fixes quietly. What it can't fix and
isn't just discomfort is a danger: smoke, a leak, gas, carbon monoxide.
Those are raised at once, whatever the mood, asleep or awake.

The house reacts first, by itself (Home Assistant automations - see
homeassistant/virtual_house.py): a leak closes the water, gas closes the
gas, smoke stops the ventilation and turns every light on. This watcher is
the voice: it follows Home Assistant's state changes over its websocket
(instant, no polling), and when a danger sensor goes off it waits a moment
for those reflexes, reads what actually happened and says so - "Воду
перекрыла" only if the valve really is closed.
"""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass

from app.config import settings
from app.ha_client import HomeAssistantClient, HomeAssistantError

# Home Assistant binary_sensor device classes that mean danger.
DANGER_WORDS = {
    "smoke": "дым",
    "moisture": "протечка",
    "gas": "утечка газа",
    "carbon_monoxide": "угарный газ",
}
REFLEX_DELAY_SECONDS = 2.0  # the house's own automations act first
RECONNECT_SECONDS = (5, 60)  # first retry, and the most it backs off to


@dataclass(frozen=True)
class Alert:
    kind: str  # a DANGER_WORDS key
    room: str
    active: bool  # False: the sensor went quiet again
    text: str

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.room}"


def danger_change(event: dict) -> tuple[str, str, bool] | None:
    """A Home Assistant state_changed event -> (entity_id, kind, active) if
    it's a danger sensor going on or off, else None."""
    data = event.get("data") or {}
    new, old = data.get("new_state"), data.get("old_state")
    if not new or not new.get("entity_id", "").startswith("binary_sensor."):
        return None
    kind = (new.get("attributes") or {}).get("device_class")
    if kind not in DANGER_WORDS or new.get("state") not in ("on", "off"):
        return None
    before = (old or {}).get("state")
    if before == new["state"]:
        return None  # an attribute changed, not the alarm
    if before not in ("on", "off") and new["state"] == "off":
        return None  # a sensor coming online quiet is no news
    return new["entity_id"], kind, new["state"] == "on"


def reflexes(kind: str, states: list[dict]) -> list[str]:
    """What the house has actually done about it, read from its states."""
    def all_in(domain: str, state: str) -> bool:
        found = [s for s in states if s["entity_id"].startswith(f"{domain}.")]
        return bool(found) and all(s["state"] == state for s in found)

    def valve_closed(name: str) -> bool:
        return any(s["entity_id"].endswith(f"{name}_valve") and s["state"] == "off" for s in states)

    done = []
    if kind == "moisture" and valve_closed("water"):
        done.append("Воду перекрыла.")
    if kind in ("gas", "carbon_monoxide") and valve_closed("gas"):
        done.append("Газ перекрыла.")
    if kind == "smoke":
        if all_in("fan", "off"):
            done.append("Вентиляцию остановила.")
        if all_in("light", "on"):
            done.append("Свет включила везде.")
    return done


def alert_text(kind: str, room: str, active: bool, done: list[str]) -> str:
    word = DANGER_WORDS[kind]
    if active:
        return " ".join([f"Внимание! {word.capitalize()}: {room.lower()}!", *done])
    return f"Отбой: {word}, {room.lower()} - датчик больше не срабатывает."


class DangerWatcher:
    """Runs until stopped, reconnecting whenever Home Assistant goes away.
    on_alert is called from the watcher's own event loop - keep it quick."""

    def __init__(
        self,
        on_alert: Callable[[Alert], None],
        client: HomeAssistantClient | None = None,
        websocket_url: str | None = None,
        token: str | None = None,
        reflex_delay: float = REFLEX_DELAY_SECONDS,
    ):
        self._on_alert = on_alert
        self._client = client or HomeAssistantClient()
        base = settings.home_assistant_url
        self._url = websocket_url or base.replace("http://", "ws://").replace("https://", "wss://") + "/api/websocket"
        self._token = token or settings.home_assistant_token
        self._reflex_delay = reflex_delay
        self._active: set[str] = set()  # entity ids currently alarming - no double alerts
        self.stopped = False

    async def run(self) -> None:
        wait = RECONNECT_SECONDS[0]
        while not self.stopped:
            try:
                await self._session()
                wait = RECONNECT_SECONDS[0]
            except (OSError, HomeAssistantError, ValueError) as exc:
                print(f"(опасности: нет связи с Home Assistant - {exc!r}, повтор через {wait} с)")
            except Exception as exc:  # noqa: BLE001 - websockets' own errors; never stop watching
                print(f"(опасности: соединение оборвалось - {exc!r}, повтор через {wait} с)")
            await asyncio.sleep(wait)
            wait = min(wait * 2, RECONNECT_SECONDS[1])

    async def _session(self) -> None:
        import websockets

        async with websockets.connect(self._url, max_size=None) as ws:
            await ws.recv()
            await ws.send(json.dumps({"type": "auth", "access_token": self._token}))
            if json.loads(await ws.recv()).get("type") != "auth_ok":
                raise HomeAssistantError("Home Assistant rejected the token")
            await ws.send(json.dumps({"id": 1, "type": "subscribe_events", "event_type": "state_changed"}))
            # Subscribed first, then look: a danger that was already on (or
            # came on while Jarvis was off) is raised too.
            for state in await self._client.get_states():
                change = danger_change({"data": {"new_state": state, "old_state": None}})
                if change and change[2]:
                    await self.handle(*change)
            async for raw in ws:
                message = json.loads(raw)
                if message.get("type") == "event" and (change := danger_change(message.get("event") or {})):
                    asyncio.ensure_future(self.handle(*change))

    async def handle(self, entity_id: str, kind: str, active: bool) -> None:
        if active == (entity_id in self._active):
            return  # already raised (or already cleared)
        (self._active.add if active else self._active.discard)(entity_id)
        room = await self._room(entity_id)
        done = []
        if active:
            await asyncio.sleep(self._reflex_delay)
            try:
                done = reflexes(kind, await self._client.get_states())
            except HomeAssistantError:
                pass  # raise the alarm anyway - just without saying what was done
        self._on_alert(Alert(kind, room, active, alert_text(kind, room, active, done)))

    async def _room(self, entity_id: str) -> str:
        try:
            room = (await self._client.render_template(f"{{{{ area_name('{entity_id}') }}}}")).strip()
        except HomeAssistantError:
            room = ""
        return room if room and room != "None" else "дом"
