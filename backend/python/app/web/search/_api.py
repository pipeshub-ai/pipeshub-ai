"""What the providers behind a JSON API have in common."""

import hashlib
from typing import Any, ClassVar

import httpx

from app.web.deadline import Deadline
from app.web.errors import (
    NotConfigured,
    TimedOut,
    Unreachable,
    UpstreamError,
    error_for_status,
)


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

    async def post_json(self, url: str, *, headers: dict[str, str], payload: dict[str, Any], deadline: Deadline) -> dict[str, Any]:
        """POST to the API and return its JSON object, mapping every failure to a ``WebError``."""
        if deadline.expired:
            raise TimedOut(f"No time left to query {self.name}", source=self.id)
        try:
            response = await self._http.post(url, headers=headers, json=payload, timeout=deadline.remaining())
        except httpx.TimeoutException as e:
            raise TimedOut(f"{self.name} did not answer in time", source=self.id) from e
        except httpx.TransportError as e:
            raise Unreachable(f"Could not connect to {self.name}", source=self.id) from e
        error = error_for_status(response.status_code, response.headers, source=self.name)
        if error is not None:
            error.source = self.id
            raise error
        try:
            data = response.json()
        except ValueError as e:
            raise UpstreamError(f"{self.name} returned a response that is not JSON", source=self.id) from e
        if not isinstance(data, dict):
            raise UpstreamError(f"{self.name} returned an unexpected response", source=self.id)
        return data
