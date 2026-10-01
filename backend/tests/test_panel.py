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
    assert page.status_code == 200 and "app.js" in page.text
    assert page.headers["cache-control"] == "no-cache"  # an update reaches the phone at once


def test_a_day_of_readings_becomes_evenly_spaced_values():
    from datetime import datetime, timedelta, timezone

    from app.panel import series

    start = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)
    points = [{"state": "20.0", "last_changed": "2026-09-30T22:00:00+00:00"},
              {"state": "22.0", "last_changed": "2026-10-01T12:00:00+00:00"},
              {"state": "unavailable", "last_changed": "2026-10-01T13:00:00+00:00"}]
    values = series(points, start, start + timedelta(hours=24), count=5)
    assert values == [20.0, 20.0, 22.0, 22.0, 22.0]  # 00, 06, 12, 18, 24 o'clock


def test_the_phone_app_finds_jarvis_without_a_pin_and_without_using_up_tries(tmp_path, monkeypatch):
    client = _client()
    for _ in range(LOCKOUT_FAILURES + 2):  # the app asks every host of the network - never a lockout
        assert client.get("/api/hello").json() == {"app": "jarvis"}
    assert client.get("/api/ping", headers={"X-Pin": PIN}).status_code == 200

    import app.panel as panel

    monkeypatch.setattr(panel, "APK", tmp_path / "jarvis.apk")
    assert client.get("/jarvis.apk").status_code == 404  # not built yet
    (tmp_path / "jarvis.apk").write_bytes(b"PK")
    got = client.get("/jarvis.apk")
    assert got.status_code == 200 and got.headers["content-type"] == "application/vnd.android.package-archive"


def test_schedules_with_their_days_and_changing_the_days():
    from tests.test_home_features import _state

    ha = FakeHA()
    ha.states.append(_state("input_text.schedule_good_morning_days", "1,2,3,4,5"))
    client = _client(ha)
    got = {s["id"]: s for s in client.get("/api/schedules", headers={"X-Pin": PIN}).json()["schedules"]}
    assert got["schedule_good_morning"]["time"] == "07:00" and got["schedule_good_morning"]["days"] == [1, 2, 3, 4, 5]
    sunset = got["schedule_sunset_entrance"]
    assert sunset["time"] is None and sunset["days"] == [1, 2, 3, 4, 5, 6, 7] and not sunset["days_editable"]

    change = lambda body: client.post("/api/schedules", headers={"X-Pin": PIN}, json=body).json()  # noqa: E731
    assert "schedules" in change({"action": "set_days", "id": "schedule_good_morning", "days": [6, 7, 9]})
    assert ha.calls[-1] == ("input_text", "set_value", "input_text.schedule_good_morning_days", {"value": "6,7"})
    assert "error" in change({"action": "set_days", "id": "schedule_good_morning", "days": []})  # off is the switch
    assert "error" in change({"action": "set_days", "id": "schedule_sunset_entrance", "days": [1]})  # no days helper
    change({"action": "disable", "name": "«Доброе утро» по будням"})
    assert ha.calls[-1] == ("automation", "turn_off", "automation.schedule_good_morning", None)


def test_scenes_say_what_they_do_and_when_they_last_ran():
    from tests.test_home_features import _state

    class WithScripts(FakeHA):
        async def get_script_config(self, object_id):
            return {"description": "Свет и розетки выключены, охрана через 2 минуты. Фразы: я ушёл, я ухожу"}

    ha = WithScripts()
    ha.states.append(_state("script.ya_ushel", "off", friendly_name="Я ушёл", last_triggered="2026-10-01T08:41:00+07:00"))
    scenes = _client(ha).get("/api/scenes", headers={"X-Pin": PIN}).json()["scenes"]
    assert scenes == [{"name": "Я ушёл", "does": scenes[0]["does"], "last": "2026-10-01T08:41:00+07:00"}]
    assert "Фразы" not in scenes[0]["does"] and "охрана" in scenes[0]["does"]


def test_the_guard_arms_with_time_to_walk_out_and_disarms_stopping_the_countdown():
    from tests.test_home_features import _state

    class WithJournal(FakeHA):
        async def get_histories(self, entity_ids, start, end):
            return {}

        async def get_logbook(self, start, end):
            return []

        async def fire_event(self, event_type, data=None):
            self.events.append(event_type)

    ha = WithJournal()
    ha.events = []
    ha.states.append(_state("automation.ukhod", "on", id="security_arm_on_leaving", current=0))
    client = _client(ha)
    view = client.get("/api/security", headers={"X-Pin": PIN}).json()
    assert view["armed"] is False and view["arming"] is False and view["can_delay"] and view["journal"] == []

    act = lambda action: client.post("/api/security", headers={"X-Pin": PIN}, json={"action": action}).json()  # noqa: E731
    act("arm")
    assert ha.events == ["jarvis_arm_soon"]  # the countdown starts and the call returns at once
    act("disarm")
    assert ha.calls[-3:] == [("automation", "turn_off", "automation.ukhod", None),
                             ("automation", "turn_on", "automation.ukhod", None),
                             ("input_boolean", "turn_off", "input_boolean.security_armed", None)]
    act("arm_now")
    assert ha.calls[-1] == ("input_boolean", "turn_on", "input_boolean.security_armed", None)
    assert "error" in act("explode")
