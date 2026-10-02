"""Electricity for the panel's Energy screen: the house's kWh by hour (today),
by day (this week, the last 30 days), against yesterday, and where it went.

All from the meters' own history - sensor.house_energy (the whole house) and
sensor.energy_<group> (climate, light, sockets) - as a real flat's meters would
give it: usage over a span is the meter at its end minus the meter at its start.
"""

from __future__ import annotations

from datetime import datetime, timedelta

HOUSE_METER = "sensor.house_energy"
GROUPS = {"climate": "Климат", "light": "Свет", "sockets": "Розетки и приборы"}
PERIODS = ("day", "week", "month")
WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


def edges(period: str, now: datetime) -> tuple[list[datetime], int]:
    """The bucket boundaries (n buckets -> n+1 edges) and which bucket is the current one."""
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "day":
        return [day + timedelta(hours=h) for h in range(25)], now.hour
    if period == "week":
        monday = day - timedelta(days=now.weekday())
        return [monday + timedelta(days=d) for d in range(8)], now.weekday()
    first = day - timedelta(days=29)  # the last 30 days, today the last bar
    return [first + timedelta(days=d) for d in range(31)], 29


def readings(series: list[dict]) -> list[tuple[datetime, float]]:
    out = []
    for item in series:
        try:
            out.append((datetime.fromisoformat(item["last_changed"]), float(item["state"])))
        except (KeyError, TypeError, ValueError):
            continue  # unknown / unavailable
    return out


def meter_at(points: list[tuple[datetime, float]], at: datetime) -> float | None:
    """The meter's reading at a moment: the last one at or before it; None before it existed."""
    value = None
    for when, reading in points:
        if when > at:
            break
        value = reading
    return value


def used(points: list[tuple[datetime, float]], start: datetime, end: datetime) -> float | None:
    """kWh between two moments; a meter that only appeared later counts from its first reading."""
    if not points or end <= points[0][0]:
        return None
    first = meter_at(points, start)
    last = meter_at(points, end)
    if last is None:
        return None
    if first is None:
        first = points[0][1]
    return max(0.0, round(last - first, 3))


def axis(period: str, bucket_edges: list[datetime]) -> list[str]:
    if period == "day":
        return ["00", "06", "12", "18", "24"]
    if period == "week":
        return WEEKDAYS
    first, last = bucket_edges[0], bucket_edges[-2]
    middle = [bucket_edges[10], bucket_edges[20]]
    return [f"{first.day} {MONTHS[first.month - 1]}", *(str(d.day) for d in middle), f"{last.day} {MONTHS[last.month - 1]}"]


async def energy_view(client, period: str, now: datetime) -> dict:
    """Bars (kWh per bucket, None for the future or before the meter), the total, the
    comparison line, and the groups' share of the period."""
    bucket_edges, current = edges(period, now)
    start = bucket_edges[0] - timedelta(days=1)  # yesterday too, for "чем вчера к этому часу"
    meters = [HOUSE_METER, *(f"sensor.energy_{g}" for g in GROUPS)]
    histories = await client.get_histories(meters, start.isoformat(), now.isoformat())
    house = readings(histories.get(HOUSE_METER, []))
    bars = []
    for i in range(len(bucket_edges) - 1):
        if i > current:
            bars.append(None)
            continue
        bars.append(used(house, bucket_edges[i], min(bucket_edges[i + 1], now)))
    total = round(sum(b for b in bars if b), 2)
    view = {"period": period, "bars": bars, "current": current, "axis": axis(period, bucket_edges), "total": total}
    if period == "day":
        day = bucket_edges[0]
        yesterday = used(house, day - timedelta(days=1), now - timedelta(days=1))
        view["yesterday_so_far"] = yesterday
    else:
        days = sum(1 for b in bars if b is not None)
        view["per_day"] = round(total / days, 2) if days else None
    split = []
    group_points = {g: readings(histories.get(f"sensor.energy_{g}", [])) for g in GROUPS}
    firsts = [p[0][0] for p in group_points.values() if p]
    since = max(bucket_edges[0], min(firsts)) if firsts else bucket_edges[0]
    for group, name in GROUPS.items():
        kwh = used(group_points[group], since, now)
        if kwh is not None:
            split.append({"name": name, "kwh": kwh})
    known = sum(s["kwh"] for s in split)
    house_since = used(house, since, now) or 0  # over the same span: the groups' meters may be newer than the period
    if split and house_since > known + 0.01:  # what no group meter counts
        split.append({"name": "Прочее", "kwh": round(house_since - known, 3)})
    view["split"] = split
    return view
