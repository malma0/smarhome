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
            resp = await shared_client().request(
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
