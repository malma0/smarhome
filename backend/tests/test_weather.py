import asyncio

from app.domains import weather
from app.tools.registry import TurnContext

FORECAST = {
    "current": {"temperature_2m": 6.4, "apparent_temperature": 3.2, "weather_code": 1,
                "wind_speed_10m": 2.1, "relative_humidity_2m": 78},
    "daily": {"time": ["2026-09-26", "2026-09-27"], "weather_code": [3, 61],
              "temperature_2m_min": [5.2, 6.0], "temperature_2m_max": [11.4, 10.6],
              "precipitation_probability_max": [0, 80], "precipitation_sum": [0.0, 4.2],
              "wind_speed_10m_max": [3.1, 6.8]},
}


def test_the_forecast_in_words():
    out = weather.describe(FORECAST, 2)
    assert out["now"] == {"temperature": "6 °C", "feels_like": "3 °C", "weather": "преимущественно ясно",
                          "wind": "2 м/с", "humidity": "78 %"}
    assert out["days"][1] == {"date": "2026-09-27", "weekday": "воскресенье", "weather": "небольшой дождь",
                              "temperature": "6..11 °C", "precipitation_chance": "80 %",
                              "precipitation": "4.2 мм", "wind_max": "7 м/с"}


def _fake_service(monkeypatch, calls):
    async def fake_get(url, params):
        calls.append((url, params))
        if url == weather.GEOCODING_URL:
            if params["name"] == "Нигдеград":
                return {}
            return {"results": [{"name": "Новосибирск", "latitude": 55.04, "longitude": 82.93}]}
        return FORECAST

    monkeypatch.setattr(weather, "_get", fake_get)
    monkeypatch.setattr(weather, "_places", {})


def test_home_city_by_default_and_the_city_is_looked_up_once(monkeypatch):
    calls = []
    _fake_service(monkeypatch, calls)
    handler = weather.make_handler("Новосибирск")
    first = asyncio.run(handler({"days": 2}, TurnContext()))
    asyncio.run(handler({}, TurnContext()))
    assert first["place"] == "Новосибирск" and len(first["days"]) == 2
    assert [url for url, _ in calls].count(weather.GEOCODING_URL) == 1


def test_unknown_city_and_no_home_city(monkeypatch):
    _fake_service(monkeypatch, [])
    assert "no city" in asyncio.run(weather.make_handler("")({"city": "Нигдеград"}, TurnContext()))["error"]
    assert "WEATHER_CITY" in asyncio.run(weather.make_handler("")({}, TurnContext()))["error"]


def test_the_service_being_down_is_an_answer_not_a_crash(monkeypatch):
    async def down(url, params):
        raise weather.WeatherError("no connection to the weather service")

    monkeypatch.setattr(weather, "_get", down)
    monkeypatch.setattr(weather, "_places", {})
    assert "no connection" in asyncio.run(weather.make_handler("Омск")({}, TurnContext()))["error"]
