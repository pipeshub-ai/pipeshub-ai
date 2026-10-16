"""What the providers behind a JSON API have in common."""

import hashlib
from typing import Any, ClassVar

import httpx

from app.web.deadline import Deadline
from app.web.endpoint import request_json
from app.web.errors import NotConfigured


class KeyedProvider:
    """A search API reached with one API key over a shared HTTP client."""

    id: ClassVar[str]
    name: ClassVar[str]

    def __init__(self, configuration: dict[str, Any], http: httpx.AsyncClient) -> None:
        self._api_key = str(configuration.get("apiKey") or "")
        self._http = http

    @property
    def cooldown_key(self) -> str:
        # A rejected or exhausted key is remembered per key, never the key itself.
        return f"{self.id}:{hashlib.sha256(self._api_key.encode()).hexdigest()[:12]}"

    def api_key(self) -> str:
        if not self._api_key:
            raise NotConfigured(f"{self.name} API key is required", source=self.id)
        return self._api_key

    async def get_json(self, url: str, *, headers: dict[str, str], params: dict[str, Any], deadline: Deadline) -> dict[str, Any]:
        return await request_json(
            self._http, "GET", url, source=self.id, name=self.name, deadline=deadline, headers=headers, params=params,
        )

    async def post_json(self, url: str, *, headers: dict[str, str], payload: dict[str, Any], deadline: Deadline) -> dict[str, Any]:
        return await request_json(
            self._http, "POST", url, source=self.id, name=self.name, deadline=deadline, headers=headers, json=payload,
        )
