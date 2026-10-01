"""The control panel's server: the PIN, the lockout, what the screen gets,
and that controls go through the house's own rules."""

from fastapi.testclient import TestClient

from app.panel import LOCKOUT_FAILURES, create_app
from tests.test_home_features import FakeHA

PIN = "test-pin-1234"


def _client(ha=None):
    return TestClient(create_app(client=ha or FakeHA(), pin=PIN))


def test_nothing_without_the_right_pin_and_a_lockout_after_five_tries():
    assert _client().get("/api/house").status_code == 401  # no PIN at all (counts as a try too)
    client = _client()
    for _ in range(LOCKOUT_FAILURES - 1):
        assert client.get("/api/ping", headers={"X-Pin": "0000"}).status_code == 401
    assert client.get("/api/ping", headers={"X-Pin": PIN}).status_code == 200  # 4 wrong: still in
    client.get("/api/ping", headers={"X-Pin": "0000"})
    assert client.get("/api/ping", headers={"X-Pin": PIN}).status_code == 429  # 5 wrong: locked out


def test_no_pin_set_means_no_way_in():
    client = TestClient(create_app(client=FakeHA(), pin=""))
    assert client.get("/api/ping", headers={"X-Pin": ""}).status_code == 401


def test_the_screen_gets_rooms_devices_readings_and_the_house():
    rooms = _client().get("/api/house", headers={"X-Pin": PIN}).json()["rooms"]
    bedroom = next(r for r in rooms if r["name"] == "Спальня")
    kinds = {d["type"]: d for d in bedroom["devices"]}
    assert kinds["curtains"]["position"] == 50 and kinds["humidifier"]["on"] is True
    assert bedroom["readings"]["window"] == {"value": True}
    assert bedroom["norms"]["humidity_min"]["value"] == 40.0
    house = next(r for r in rooms if r["name"] == "Весь дом")
    assert house["house"]["power_now"]["value"] == "1054"
    assert house["devices"][0]["type"] == "security"


def test_controls_go_through_the_house_rules():
    ha = FakeHA()
    client = _client(ha)
    done = client.post("/api/control", headers={"X-Pin": PIN},
                       json={"room": "Спальня", "device": "curtains", "action": "on", "position": 30, "entity_id": "x"})
    assert "done" in done.json()
    assert ha.calls[-1] == ("cover", "set_cover_position", "cover.bedroom_curtains", {"position": 30})
    missing = client.post("/api/control", headers={"X-Pin": PIN}, json={"room": "Спальня", "device": "ac", "action": "on"})
    assert "error" in missing.json()  # the bedroom here has no AC - said, not invented


def test_the_panel_page_is_served():
    client = _client()
    assert client.get("/", follow_redirects=False).headers["location"] == "/panel/"
    page = client.get("/panel/")
    assert page.status_code == 200 and "panel.js" in page.text
