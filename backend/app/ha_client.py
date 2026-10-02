"""Home Assistant REST API. Requests go through app.http_client's shared
client: a fresh httpx.AsyncClient per request measured 3.3 s for one house
status read on this laptop (each one reloads the certificate bundle, even
for plain-http localhost)."""

from typing import Any

import httpx

from app.config import settings
from app.http_client import shared_client

TIMEOUT_SECONDS = 10


class HomeAssistantError(RuntimeError):
    pass


class HomeAssistantClient:
    def __init__(self, base_url: str | None = None, token: str | None = None):
        self.base_url = (base_url or settings.home_assistant_url).rstrip("/")
        self.token = token or settings.home_assistant_token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Network failures (Home Assistant not running...) come out as
        HomeAssistantError too - one exception type for callers to handle."""
        try:
            resp = await shared_client(self.base_url).request(
                method, f"{self.base_url}{path}", headers=self._headers(), timeout=TIMEOUT_SECONDS, **kwargs
            )
        except httpx.HTTPError as exc:
            raise HomeAssistantError(f"{method} {path}: {exc!r}") from exc
        if resp.status_code != 200:
            raise HomeAssistantError(f"{method} {path} failed: {resp.status_code} {resp.text}")
        return resp

    async def get_states(self) -> list[dict[str, Any]]:
        return (await self._request("GET", "/api/states")).json()

    async def get_state(self, entity_id: str) -> dict[str, Any]:
        return (await self._request("GET", f"/api/states/{entity_id}")).json()

    async def render_template(self, template: str) -> str:
        """Home Assistant's template engine - the only way the REST API
        exposes areas (which room an entity is in)."""
        return (await self._request("POST", "/api/template", json={"template": template})).text

    async def get_script_config(self, object_id: str) -> dict[str, Any]:
        """A script's own definition (alias, description, sequence) - the
        description is where a scenario keeps the phrases that start it."""
        return (await self._request("GET", f"/api/config/script/config/{object_id}")).json()

    async def save_script_config(self, object_id: str, config: dict[str, Any]) -> None:
        """Writes a script into scripts.yaml and reloads scripts - a scenario made in the app."""
        await self._request("POST", f"/api/config/script/config/{object_id}", json=config)

    async def delete_script_config(self, object_id: str) -> None:
        await self._request("DELETE", f"/api/config/script/config/{object_id}")

    async def get_automation_config(self, automation_id: str) -> dict[str, Any]:
        """An automation from automations.yaml (the ones made in the app) - by its id, not entity id."""
        return (await self._request("GET", f"/api/config/automation/config/{automation_id}")).json()

    async def save_automation_config(self, automation_id: str, config: dict[str, Any]) -> None:
        await self._request("POST", f"/api/config/automation/config/{automation_id}", json=config)

    async def delete_automation_config(self, automation_id: str) -> None:
        await self._request("DELETE", f"/api/config/automation/config/{automation_id}")

    async def get_history(self, entity_id: str, start: str, end: str) -> list[dict[str, Any]]:
        """[{"state", "last_changed"}, ...] of one entity between two ISO times."""
        response = await self._request(
            "GET", f"/api/history/period/{start}",
            params={"filter_entity_id": entity_id, "end_time": end, "minimal_response": "", "no_attributes": ""},
        )
        series = response.json()
        return series[0] if series else []

    async def get_histories(self, entity_ids: list[str], start: str, end: str) -> dict[str, list[dict[str, Any]]]:
        """Several entities at once: {entity_id: [{"state", "last_changed"}, ...]} - the journal's sensors."""
        if not entity_ids:
            return {}
        response = await self._request(
            "GET", f"/api/history/period/{start}",
            params={"filter_entity_id": ",".join(entity_ids), "end_time": end, "minimal_response": "",
                    "no_attributes": ""},
        )
        found = {}
        for series in response.json():
            if series:
                found[series[0]["entity_id"]] = series
        return found

    async def get_logbook(self, start: str, end: str) -> list[dict[str, Any]]:
        """What happened and why ("context_name": the automation that did it) between two ISO times."""
        return (await self._request("GET", f"/api/logbook/{start}", params={"end_time": end})).json()

    async def fire_event(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """An event for automations to start from - returns at once, unlike automation.trigger."""
        await self._request("POST", f"/api/events/{event_type}", json=data or {})

    async def call_service(
        self,
        domain: str,
        service: str,
        entity_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any:
        payload: dict[str, Any] = dict(data or {})
        if entity_id:
            payload["entity_id"] = entity_id
        return (await self._request("POST", f"/api/services/{domain}/{service}", json=payload)).json()


ha_client = HomeAssistantClient()
