"""The plan from the layout editor: only what the plan can draw is kept, and the panel
serves and resets it."""

import pytest
from fastapi.testclient import TestClient

from app import layout
from app.panel import create_app
from tests.test_home_features import FakeHA

PIN = "test-pin-1234"
PLAN = {"rooms": {"Кухня": {"x": 0, "y": 0, "w": 140, "h": 200}},
        "devices": {"Кухня|light": [70, 100]},
        "doors": [["h", 200, 50, 84]], "windows": [["v", 0, 40, 120, "Кухня"]],
        "front": {"room": "Кухня", "x": 120, "y": 180}, "junk": "dropped"}


def test_only_what_the_plan_draws_is_kept():
    good = layout.clean(PLAN)
    assert "junk" not in good and good["rooms"]["Кухня"]["w"] == 140.0 and good["windows"][0][4] == "Кухня"
    for bad in ({"rooms": {}}, {"rooms": {"Кухня": {"x": 0, "y": 0, "w": 5, "h": 5}}},
                {"rooms": {"Кухня": {"x": "0", "y": 0, "w": 90, "h": 90}}},
                {"rooms": {"Кухня": {"x": 0, "y": 0, "w": 90, "h": 90}}, "doors": [["d", 1, 2, 3]]},
                {"rooms": {"Кухня": {"x": 0, "y": 0, "w": 90, "h": 90}}, "devices": {"light": [1, 2]}}):
        with pytest.raises(ValueError):
            layout.clean(bad)


def test_the_panel_saves_serves_and_resets_the_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(layout, "FILE", tmp_path / "layout.json")
    client = TestClient(create_app(client=FakeHA(), pin=PIN))
    h = {"X-Pin": PIN}
    assert client.get("/api/layout", headers=h).json() == {"layout": None}  # never drawn
    saved = client.put("/api/layout", headers=h, json={"layout": PLAN}).json()["layout"]
    assert client.get("/api/layout", headers=h).json()["layout"] == saved
    assert "error" in client.put("/api/layout", headers=h, json={"layout": {"rooms": {}}}).json()
    assert client.put("/api/layout", headers=h, json={"layout": None}).json() == {"layout": None}
    assert not (tmp_path / "layout.json").exists()
