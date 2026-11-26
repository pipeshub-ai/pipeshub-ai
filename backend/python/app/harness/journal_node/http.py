"""``NodeServiceClient``: the one HTTP client the harness uses for Node's internal routes."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from app.services.base_client import BaseServiceClient

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


class NodeServiceClient(BaseServiceClient):
    """One pooled connection to Node; one attempt per call.

    The base's ``_request_with_retry`` is not used: every caller owns retry and classification (fences, leases,
    idempotent command ids, the claimer's backoff), so a retry or an open breaker here would change their outcome.
    """

    def __init__(
        self,
        *,
        base_url: str,
        timeout_s: float,
        http_client: httpx.AsyncClient | None = None,
        service_name: str = "node",
    ) -> None:
        super().__init__(
            base_url,
            service_name=service_name,
            connect_timeout=timeout_s,
            read_timeout=timeout_s,
            write_timeout=timeout_s,
            pool_timeout=timeout_s,
            max_retries=1,
        )
        self._client = http_client or httpx.AsyncClient(
            base_url=self.service_url, timeout=self._timeout, follow_redirects=False
        )

    async def send(
        self,
        method: str,
        path: str,
        *,
        bearer: str | None,
        json: object = None,
        params: Mapping[str, str | int] | None = None,
        content: bytes | None = None,
        content_type: str | None = None,
        unreachable: Callable[[httpx.HTTPError], Exception] | None = None,
    ) -> httpx.Response:
        """Node's answer, whatever its status; a transport failure becomes ``unreachable(exc)`` when given."""
        headers: dict[str, str] = {}
        if bearer is not None:
            headers["Authorization"] = f"Bearer {bearer}"
        if content_type is not None:
            headers["Content-Type"] = content_type
        try:
            return await self._client.request(
                method, path, json=json, params=params, content=content, headers=headers
            )
        except httpx.HTTPError as exc:
            if unreachable is None:
                raise
            raise unreachable(exc) from exc

    async def aclose(self) -> None:
        """Closes the client, a lent one too: a caller that keeps using a client it lent does not call this."""
        await self._client.aclose()


__all__ = ["NodeServiceClient"]
