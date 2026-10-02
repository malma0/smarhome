"""The security journal: sensor history -> lines, the all-clear on the alarm's own
line, repeated movement folded, and what the house did after an alarm."""

from app import journal

T = "2026-10-01T10:{:02d}:00+00:00"


def _states():
    s = lambda e, cls, name: {"entity_id": e, "state": "off", "attributes": {"device_class": cls, "friendly_name": name}}  # noqa: E731
    return [s("binary_sensor.kitchen_leak", "moisture", "Кухня: протечка"),
            s("binary_sensor.hall_motion", "motion", "Зал: движение"),
            s("binary_sensor.kitchen_window", "window", "Кухня: окно"),
            s("sensor.kitchen_temperature", "temperature", "Кухня: температура"),
            {"entity_id": journal.GUARD, "state": "on", "attributes": {}}]


def test_only_what_the_journal_follows():
    follow = journal.watched(_states())
    assert follow == {"binary_sensor.kitchen_leak": ("moisture", "Кухня"), "binary_sensor.hall_motion": ("motion", "Зал"),
                      "binary_sensor.kitchen_window": ("window", "Кухня"), journal.GUARD: ("guard", "")}


def test_changes_become_lines_newest_first():
    follow = journal.watched(_states())
    rec = lambda state, minute: {"state": state, "last_changed": T.format(minute)}  # noqa: E731
    histories = {
        "binary_sensor.kitchen_leak": [rec("off", 0), rec("on", 10), rec("off", 15)],
        "binary_sensor.hall_motion": [rec("off", 0), rec("on", 1), rec("off", 2), rec("on", 5), rec("off", 6), rec("on", 30)],
        "binary_sensor.kitchen_window": [rec("on", 0), rec("off", 20)],  # open when the period began: not an event
        journal.GUARD: [rec("off", 0), rec("unavailable", 3), rec("on", 40)],
    }
    lines = journal.events(follow, histories)
    assert [x["title"] for x in lines] == ["Охрана включена", "Движение: Зал", "Закрыто окно: Кухня",
                                           "Тревога: протечка — Кухня", "Движение: Зал"]  # the 10:05 movement folded in
    alarm = lines[3]
    assert alarm["k"] == "alarm" and alarm["icon"] == "leak" and alarm["cleared"] == T.format(15)


def test_what_the_house_did_after_the_alarm():
    alarm = {"at": T.format(10)}
    logbook = [
        {"entity_id": "switch.kitchen_water_valve", "name": "Кухня: кран воды", "state": "off",
         "when": "2026-10-01T10:10:02+00:00", "context_name": "Опасность: протечка - перекрыть воду"},
        {"entity_id": "input_boolean.kitchen_water_valve_power", "name": "мотор", "state": "off",
         "when": "2026-10-01T10:10:02+00:00", "context_name": "Опасность: протечка - перекрыть воду"},  # a knob behind it
        {"entity_id": "light.hall", "name": "Зал: свет", "state": "on", "when": "2026-10-01T10:10:03+00:00",
         "context_name": "Расписание: свет"},  # not about the alarm
        {"entity_id": "fan.kitchen", "name": "Кухня: вентиляция", "state": "off", "when": "2026-10-01T10:30:00+00:00",
         "context_name": "Опасность: дым"},  # long after
    ]
    assert journal.acts(alarm, logbook) == [{"t": "Кухня: кран воды — перекрыт", "at": "+2 с"}]


def test_an_alert_says_what_where_and_what_the_house_did():
    import asyncio

    from app import alerts

    class House:
        async def get_states(self):
            leak = {"entity_id": "binary_sensor.kitchen_leak", "state": "on", "last_changed": T.format(10),
                    "attributes": {"device_class": "moisture", "friendly_name": "Кухня: протечка"}}
            quiet = {"entity_id": "binary_sensor.hall_smoke", "state": "off", "last_changed": T.format(0),
                     "attributes": {"device_class": "smoke", "friendly_name": "Зал: дым"}}
            return [leak, quiet]

        async def get_logbook(self, start, end):
            return [{"entity_id": "switch.kitchen_water_valve", "name": "Кухня: кран воды", "state": "off",
                     "when": "2026-10-01T10:10:02+00:00", "context_name": "Опасность: протечка - перекрыть воду"}]

    found = asyncio.run(alerts.active(House()))
    assert len(found) == 1
    leak = found[0]
    assert (leak["title"], leak["where"], leak["icon"]) == ("Протечка", "на кухне", "leak")
    assert leak["acts"] == [{"t": "Кухня: кран воды — перекрыт", "at": "+2 с"}] and leak["advice"]
