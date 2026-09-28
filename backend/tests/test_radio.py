"""Radio without the network or a sound card: the catalog and the player are fakes."""

import asyncio

import numpy as np

from app.domains.radio import DUCKED_GAIN, Radio, make_handler
from app.tools import router
from app.tools.registry import TurnContext


class FakeRadio(Radio):
    def __init__(self, dead=()):
        super().__init__()
        self.dead = set(dead)

    def play(self, station):
        if station["url"] in self.dead:
            raise OSError("stream down")
        self._device = object()
        self.station, self.last = station["name"], station

    def stop(self):
        was = self._device is not None
        self._device, self.station = None, None
        return was


def _run(handler, **tool_input):
    return asyncio.run(handler(tool_input, TurnContext()))


def _catalog(*stations):
    async def find(query):
        return [{"name": n, "url": u} for n, u in stations]
    return find


def test_play_status_stop_and_the_last_station_again():
    radio = FakeRadio()
    handler = make_handler(radio, _catalog(("Радио Маяк", "http://mayak")))
    assert _run(handler, action="play", station="маяк") == {"playing": "Радио Маяк"}
    assert _run(handler, action="status") == {"playing": "Радио Маяк"}
    assert _run(handler, action="stop") == {"stopped": True}
    assert _run(handler, action="status") == {"playing": None}
    assert _run(handler, action="play") == {"playing": "Радио Маяк"}  # "включи радио" - the last one


def test_a_dead_stream_falls_to_the_next_station_found():
    radio = FakeRadio(dead={"http://a"})
    handler = make_handler(radio, _catalog(("A", "http://a"), ("B", "http://b")))
    assert _run(handler, action="play", station="x") == {"playing": "B"}


def test_nothing_found_or_nothing_to_resume_is_said():
    handler = make_handler(FakeRadio(), _catalog())
    assert "error" in _run(handler, action="play", station="несуществующее")
    assert "error" in _run(handler, action="play")


def test_ducked_music_is_quieter():
    radio = Radio()
    def source():
        yield b""
        while True:
            yield np.full(4, 1000, dtype=np.int16).tobytes()
    started = source()
    next(started)  # as stream_any hands it over
    stream = radio._scaled(started)
    next(stream)
    assert np.frombuffer(stream.send(2), dtype=np.int16)[0] == 1000
    radio.duck(True)
    assert np.frombuffer(stream.send(2), dtype=np.int16)[0] == int(1000 * DUCKED_GAIN)


def test_radio_words_reach_the_radio():
    for text in ("включи радио маяк", "выключи радио", "что сейчас играет?"):
        assert "radio" in router.select(text, None), text
