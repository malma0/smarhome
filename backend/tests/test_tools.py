"""Safety-rule tests. The reject-path tests need no network access at all -
they return before any HA call. The "confirmed" pass-through tests mock
ha_client.call_service so nothing here ever needs a running Home Assistant."""

import asyncio
from unittest.mock import AsyncMock

import app.tools as tools_module
from app.tools import TurnContext, _set_room_ac, _set_room_temperature


def test_temperature_hard_limit_rejected_even_if_confirmed():
    ctx = TurnContext()
    result = asyncio.run(_set_room_temperature("living_room", 40, confirmed=True, ctx=ctx))
    assert "error" in result
    assert ctx.touched_rooms == set()


def test_temperature_soft_limit_requires_confirmation():
    ctx = TurnContext()
    result = asyncio.run(_set_room_temperature("living_room", 30, confirmed=False, ctx=ctx))
    assert "error" in result
    assert ctx.touched_rooms == set()


def test_temperature_soft_limit_allowed_with_confirmation(monkeypatch):
    monkeypatch.setattr(tools_module.ha_client, "call_service", AsyncMock(return_value={}))
    ctx = TurnContext()
    result = asyncio.run(_set_room_temperature("living_room", 30, confirmed=True, ctx=ctx))
    assert result == {"ok": True, "room": "living_room", "target_temperature_c": 30}
    assert ctx.touched_rooms == {"living_room"}


def test_unknown_room_rejected():
    ctx = TurnContext()
    result = asyncio.run(_set_room_temperature("garage", 21, confirmed=False, ctx=ctx))
    assert "error" in result


def test_second_room_in_same_turn_requires_confirmation():
    ctx = TurnContext()
    ctx.touched_rooms.add("living_room")
    result = asyncio.run(_set_room_ac("bedroom", True, confirmed=False, ctx=ctx))
    assert "error" in result
    assert "bedroom" not in ctx.touched_rooms


def test_second_room_in_same_turn_allowed_with_confirmation_flag(monkeypatch):
    monkeypatch.setattr(tools_module.ha_client, "call_service", AsyncMock(return_value={}))
    ctx = TurnContext()
    ctx.touched_rooms.add("living_room")
    result = asyncio.run(_set_room_ac("bedroom", True, confirmed=True, ctx=ctx))
    assert result == {"ok": True, "room": "bedroom", "ac_on": True}
    assert ctx.touched_rooms == {"living_room", "bedroom"}
