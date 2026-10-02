"""The Energy screen's numbers: usage per bucket from a meter's readings, the
current bucket, and the split counted only while the group meters existed."""

import asyncio
from datetime import datetime, timedelta, timezone

from app import energy

TZ = timezone(timedelta(hours=7))
NOW = datetime(2026, 10, 2, 3, 30, tzinfo=TZ)  # a Friday


def _rec(hour, minute, value, day=2):
    return {"state": str(value), "last_changed": datetime(2026, 10, day, hour, minute, tzinfo=TZ).isoformat()}


class Meters:
    def __init__(self, histories):
        self.histories = histories

    async def get_histories(self, entity_ids, start, end):
        return {e: h for e, h in self.histories.items() if e in entity_ids}


def test_buckets_and_the_current_one():
    edges, current = energy.edges("day", NOW)
    assert len(edges) == 25 and current == 3
    edges, current = energy.edges("week", NOW)
    assert edges[0].weekday() == 0 and current == 4
    edges, current = energy.edges("month", NOW)
    assert len(edges) == 31 and current == 29


def test_usage_is_the_meter_at_the_end_minus_at_the_start():
    points = energy.readings([_rec(0, 0, 10.0), _rec(1, 0, 10.5), _rec(2, 30, 11.25), {"state": "unknown", "last_changed": "x"}])
    at = lambda h: NOW.replace(hour=h, minute=0)  # noqa: E731
    assert energy.used(points, at(0), at(1)) == 0.5
    assert energy.used(points, at(1), at(3)) == 0.75
    assert energy.used(points, NOW - timedelta(days=1), at(1)) == 0.5  # a meter that came later counts from its start
    assert energy.used(points, at(0) - timedelta(days=1), at(0) - timedelta(hours=1)) is None


def test_the_view_compares_with_yesterday_and_splits_only_the_metered_span():
    house = [_rec(3, 0, 5.0, day=1), _rec(3, 30, 5.5, day=1), _rec(0, 0, 10.0), _rec(1, 0, 11.0), _rec(3, 0, 12.0)]
    climate = [_rec(1, 0, 0.0), _rec(3, 0, 0.75)]  # the group meter appeared at 01:00
    view = asyncio.run(energy.energy_view(Meters({energy.HOUSE_METER: house, "sensor.energy_climate": climate}), "day", NOW))
    assert view["bars"][:4] == [1.0, 0.0, 1.0, 0.0]  # a reading lands in its own hour
    assert view["bars"][4] is None and view["total"] == 2.0
    assert view["yesterday_so_far"] == 0.5
    assert view["split"] == [{"name": "Климат", "kwh": 0.75}, {"name": "Прочее", "kwh": 0.25}]  # 01:00-now, not the whole day
