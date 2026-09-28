"""Internet radio - "включи радио Маяк", "выключи радио", "что играет?".

Stations are found by name in Radio Browser (radio-browser.info - a free,
open catalog, no key), Russian ones first; MP3 streams only, since that's
what miniaudio decodes. Played inside Jarvis, no player window. While
Jarvis listens or answers, the radio is turned down (duck) - music in the
room is exactly what speech recognition is worst at.
"""

import asyncio
import threading

import numpy as np

from app.http_client import shared_client
from app.tools.registry import Tool, ToolRegistry, TurnContext

CATALOG = "https://de1.api.radio-browser.info/json/stations/search"
HEADERS = {"User-Agent": "Jarvis-home/1.0"}  # Radio Browser asks clients to name themselves
DUCKED_GAIN = 0.2
SAMPLE_RATE, CHANNELS = 44100, 2


async def find_stations(query: str, limit: int = 5) -> list[dict]:
    """Russian stations first, then the rest of the world."""
    params = {"name": query, "codec": "MP3", "order": "clickcount", "reverse": "true", "limit": limit,
              "hidebroken": "true"}
    for extra in ({"countrycode": "RU"}, {}):
        response = await shared_client().get(CATALOG, params={**params, **extra}, headers=HEADERS, timeout=15)
        response.raise_for_status()
        stations = [{"name": s["name"].strip(), "url": s["url_resolved"]} for s in response.json() if s.get("url_resolved")]
        if stations:
            return stations
    return []


class Radio:
    """One stream at a time, on miniaudio's own threads."""

    def __init__(self):
        self._lock = threading.Lock()
        self._device = None
        self._source = None
        self.station: str | None = None
        self.last: dict | None = None
        self.gain = 1.0

    @property
    def playing(self) -> bool:
        return self._device is not None

    def play(self, station: dict) -> None:
        """Blocking (connects to the stream) - call it off the event loop."""
        import miniaudio

        self.stop()
        source = miniaudio.IceCastClient(station["url"])
        stream = miniaudio.stream_any(source, source.audio_format, output_format=miniaudio.SampleFormat.SIGNED16,
                                      nchannels=CHANNELS, sample_rate=SAMPLE_RATE)
        device = miniaudio.PlaybackDevice(output_format=miniaudio.SampleFormat.SIGNED16, nchannels=CHANNELS,
                                          sample_rate=SAMPLE_RATE)
        scaled = self._scaled(stream)
        next(scaled)  # miniaudio wants a started generator (stream_any's own already is)
        device.start(scaled)
        with self._lock:
            self._device, self._source = device, source
            self.station, self.last = station["name"], station

    def _scaled(self, stream):
        required = yield b""
        while True:
            data = stream.send(required)
            if self.gain < 1.0:
                data = (np.frombuffer(data, dtype=np.int16) * self.gain).astype(np.int16).tobytes()
            required = yield data

    def stop(self) -> bool:
        """True if something was playing."""
        with self._lock:
            device, source = self._device, self._source
            self._device = self._source = None
            self.station = None
        if device is None:
            return False
        device.close()
        source.close()
        return True

    def duck(self, on: bool) -> None:
        self.gain = DUCKED_GAIN if on else 1.0


player = Radio()


def make_handler(radio: Radio, find=find_stations):
    async def radio_tool(tool_input: dict, ctx: TurnContext) -> dict:
        action = tool_input.get("action") or "play"
        if action == "stop":
            return {"stopped": radio.stop()}
        if action == "status":
            return {"playing": radio.station} if radio.playing else {"playing": None}
        query = (tool_input.get("station") or "").strip()
        if query:
            try:
                stations = await find(query)
            except Exception as exc:  # noqa: BLE001 - the catalog being down is an answer, not a crash
                return {"error": f"The station catalog didn't answer: {exc!r}"[:200]}
        elif radio.last:
            stations = [radio.last]
        else:
            return {"error": "No station named and none played before - ask which station."}
        if not stations:
            return {"error": f"No station found for {query!r}."}
        for station in stations:  # a dead stream: the next one found
            try:
                await asyncio.to_thread(radio.play, station)
                return {"playing": station["name"]}
            except Exception:  # noqa: BLE001
                continue
        return {"error": "The stations found don't answer right now.", "tried": [s["name"] for s in stations]}

    return radio_tool


def register(registry: ToolRegistry, radio: Radio = player) -> None:
    registry.register(
        Tool(
            name="radio",
            description=(
                "Internet radio played by Jarvis. play: station by name ('Маяк', 'Европа Плюс', 'Рекорд', "
                "'relax'); without one, the last station. stop ('выключи радио'). status ('что играет?' - always ask it, never guess). "
                "Volume is the computer's volume (media tool)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["play", "stop", "status"]},
                    "station": {"type": ["string", "null"]},
                },
                "required": ["action"],
            },
            handler=make_handler(radio),
        )
    )
