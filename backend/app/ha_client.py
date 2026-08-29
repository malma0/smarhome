from typing import Any

import httpx

from app.config import settings


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

    async def get_states(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{self.base_url}/api/states", headers=self._headers())
        if resp.status_code != 200:
            raise HomeAssistantError(f"GET /api/states failed: {resp.status_code} {resp.text}")
        return resp.json()

    async def get_state(self, entity_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{self.base_url}/api/states/{entity_id}", headers=self._headers())
        if resp.status_code != 200:
            raise HomeAssistantError(f"GET /api/states/{entity_id} failed: {resp.status_code} {resp.text}")
        return resp.json()

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
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{self.base_url}/api/services/{domain}/{service}",
                headers=self._headers(),
                json=payload,
            )
        if resp.status_code != 200:
            raise HomeAssistantError(
                f"POST /api/services/{domain}/{service} failed: {resp.status_code} {resp.text}"
            )
        return resp.json()


ha_client = HomeAssistantClient()
