"""Weather: "что надеть?", "будет дождь?", "какая погода в выходные?".

Open-Meteo (open-meteo.com): free, no key, no account. The city is looked
up with its geocoding API (once per name, then remembered) and the
forecast asked for by coordinates. The home city is WEATHER_CITY in .env -
Home Assistant's own location was never set (it's still the Amsterdam it
ships with), so it isn't used.
"""

from datetime import date

import httpx

from app.http_client import shared_client
from app.tools.registry import Tool, ToolRegistry, TurnContext

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
MAX_DAYS = 7
# Today, tomorrow and the day after: asked "какая погода завтра?" with days=1 the
# model read today's forecast out as tomorrow's.
DEFAULT_DAYS = 3
WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")

# WMO weather codes, as Open-Meteo reports them.
WEATHER_CODES = {
    0: "ясно", 1: "преимущественно ясно", 2: "переменная облачность", 3: "пасмурно",
    45: "туман", 48: "туман с изморозью",
    51: "слабая морось", 53: "морось", 55: "сильная морось", 56: "ледяная морось", 57: "ледяная морось",
    61: "небольшой дождь", 63: "дождь", 65: "сильный дождь", 66: "ледяной дождь", 67: "ледяной дождь",
    71: "небольшой снег", 73: "снег", 75: "сильный снег", 77: "снежная крупа",
    80: "небольшой ливень", 81: "ливень", 82: "сильный ливень", 85: "снегопад", 86: "сильный снегопад",
    95: "гроза", 96: "гроза с градом", 99: "гроза с сильным градом",
}


class WeatherError(RuntimeError):
    pass


async def _get(url: str, params: dict) -> dict:
    try:
        resp = await shared_client().get(url, params=params, timeout=15)
    except httpx.HTTPError as exc:
        raise WeatherError(f"no connection to the weather service: {exc!r}") from exc
    if resp.status_code != 200:
        raise WeatherError(f"weather service answered {resp.status_code}")
    return resp.json()


_places: dict[str, dict] = {}


async def find_place(city: str) -> dict:
    key = city.strip().casefold()
    if key not in _places:
        found = (await _get(GEOCODING_URL, {"name": city.strip(), "count": 1, "language": "ru"})).get("results")
        if not found:
            raise WeatherError(f"no city called '{city}'")
        place = found[0]
        _places[key] = {"name": place["name"], "latitude": place["latitude"], "longitude": place["longitude"],
                        "region": place.get("admin1"), "country": place.get("country")}
    return _places[key]


def describe(forecast: dict, days: int) -> dict:
    """Open-Meteo's answer -> what the model needs, in words."""
    cur = forecast["current"]
    now = {
        "temperature": f"{cur['temperature_2m']:.0f} °C",
        "feels_like": f"{cur['apparent_temperature']:.0f} °C",
        "weather": WEATHER_CODES.get(cur["weather_code"], "?"),
        "wind": f"{cur['wind_speed_10m']:.0f} м/с",
        "humidity": f"{cur['relative_humidity_2m']:.0f} %",
    }
    daily = forecast["daily"]
    out_days = []
    for i in range(min(days, len(daily["time"]))):
        day = date.fromisoformat(daily["time"][i])
        out_days.append({
            "date": daily["time"][i],
            "weekday": WEEKDAYS[day.weekday()],
            "weather": WEATHER_CODES.get(daily["weather_code"][i], "?"),
            "temperature": f"{daily['temperature_2m_min'][i]:.0f}..{daily['temperature_2m_max'][i]:.0f} °C",
            "precipitation_chance": f"{daily['precipitation_probability_max'][i] or 0:.0f} %",
            "precipitation": f"{daily['precipitation_sum'][i] or 0:.1f} мм",
            "wind_max": f"{daily['wind_speed_10m_max'][i]:.0f} м/с",
        })
    return {"now": now, "days": out_days}


def make_handler(home_city: str):
    async def get_weather(tool_input: dict, ctx: TurnContext) -> dict:
        city = (tool_input.get("city") or "").strip() or home_city
        if not city:
            return {"error": "The home city isn't set (WEATHER_CITY in .env) - ask which city."}
        days = max(1, min(MAX_DAYS, int(tool_input.get("days") or DEFAULT_DAYS)))
        try:
            place = await find_place(city)
            forecast = await _get(FORECAST_URL, {
                "latitude": place["latitude"], "longitude": place["longitude"], "timezone": "auto",
                "forecast_days": days, "wind_speed_unit": "ms",
                "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m",
                "daily": "weather_code,temperature_2m_min,temperature_2m_max,precipitation_probability_max,"
                         "precipitation_sum,wind_speed_10m_max",
            })
        except WeatherError as exc:
            return {"error": str(exc)}
        return {"place": place["name"], **describe(forecast, days)}

    return get_weather


def register(registry: ToolRegistry, home_city: str) -> None:
    registry.register(
        Tool(
            name="get_weather",
            description=(
                "Weather now and the forecast. days = how many days from today (default 3: today, tomorrow, "
                "the day after). 'Что надеть' - from temperature, feels_like, wind, precipitation. No city = "
                "home city."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "city": {"type": ["string", "null"]},
                    "days": {"type": ["integer", "null"], "minimum": 1, "maximum": MAX_DAYS},
                },
            },
            handler=make_handler(home_city),
        )
    )
