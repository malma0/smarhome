"""Exchange rates - "сколько стоит доллар", "курс евро", "100 юаней в рублях".

The Central Bank of Russia's official daily rates, through the free JSON
mirror cbr-xml-daily.ru (no key). A rate is set once a day, so one answer is
kept for an hour instead of asking again for every question.
"""

import time

from app.http_client import shared_client
from app.tools.registry import Tool, ToolRegistry, TurnContext

URL = "https://www.cbr-xml-daily.ru/daily_json.js"
CACHE_SECONDS = 3600
DEFAULT = ("USD", "EUR", "CNY")


def rates_from(data: dict, codes: list[str]) -> dict:
    """The feed's rates per ONE unit ("Nominal" is 100 for the tenge, 10 for the lira)."""
    valute = data["Valute"]
    out, unknown = {}, []
    for code in codes:
        item = valute.get(code.upper())
        if item is None:
            unknown.append(code)
            continue
        nominal = item["Nominal"] or 1
        value, previous = item["Value"] / nominal, item["Previous"] / nominal
        out[code.upper()] = {"name": item["Name"], "rub": round(value, 4), "change_since_previous": round(value - previous, 4)}
    result = {"date": data["Date"][:10], "rates": out}
    if unknown:
        result["unknown_codes"] = unknown
    return result


def make_handler(fetch=None, clock=time.monotonic):
    cache: dict = {}

    async def default_fetch() -> dict:
        response = await shared_client().get(URL, timeout=15)
        response.raise_for_status()
        return response.json()

    async def exchange_rates(tool_input: dict, ctx: TurnContext) -> dict:
        codes = [str(c).strip() for c in (tool_input.get("currencies") or DEFAULT) if str(c).strip()]
        if not cache or clock() - cache["at"] > CACHE_SECONDS:
            try:
                cache.update(data=await (fetch or default_fetch)(), at=clock())
            except Exception as exc:  # noqa: BLE001 - no rates is an answer, not a crash
                return {"error": f"The Central Bank's rates didn't load: {exc!r}"[:200]}
        return rates_from(cache["data"], codes)

    return exchange_rates


def register(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            name="exchange_rates",
            description=(
                "Official Central Bank of Russia rates in rubles per ONE unit, and the change since the previous "
                "rate. currencies: ISO codes (USD, EUR, CNY, KZT, TRY, GBP, JPY...); default USD, EUR, CNY. "
                "For '100 евро в рублях' multiply. Say the date only if it isn't today."
            ),
            parameters={
                "type": "object",
                "properties": {"currencies": {"type": ["array", "null"], "items": {"type": "string"}}},
            },
            handler=make_handler(),
        )
    )
