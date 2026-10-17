"""Calls to the web services a provider speaks JSON with: hosted APIs and admin-run servers."""

import hashlib
from typing import Any

import httpx

from app.utils.model_egress import guarded_async_client
from app.web.deadline import Deadline
from app.web.errors import (
    NotConfigured,
    TimedOut,
    Unreachable,
    UpstreamError,
    error_for_status,
)


def fingerprint(secret: str) -> str:
    """A short, one-way tag for a credential, to tell keys apart without keeping them."""
    # A key derivation function, not a bare hash: the tag must not make a short key easy to guess.
    return hashlib.pbkdf2_hmac("sha256", secret.encode(), b"pipeshub-web-cooldown", 10_000).hex()[:12]


async def request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    source: str,
    name: str,
    deadline: Deadline,
    headers: dict[str, str] | None = None,
    json: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The JSON object ``url`` answers with. Every failure is a ``WebError`` from ``source``."""
    if deadline.expired:
        raise TimedOut(f"No time left to query {name}", source=source)
    try:
        response = await client.request(method, url, headers=headers, json=json, params=params, timeout=deadline.remaining())
    except httpx.TimeoutException as e:
        raise TimedOut(f"{name} did not answer in time", source=source) from e
    except httpx.TransportError as e:
        raise Unreachable(f"Could not connect to {name}", source=source) from e
    error = error_for_status(response.status_code, response.headers, source=name)
    if error is not None:
        error.source = source
        raise error
    try:
        data = response.json()
    except ValueError as e:
        raise UpstreamError(f"{name} returned a response that is not JSON", source=source) from e
    if not isinstance(data, dict):
        raise UpstreamError(f"{name} returned an unexpected response", source=source)
    return data


class Endpoint:
    """A server an admin points PipesHub at (often self-hosted, so often on a private network).

    Its address follows the policy for admin-configured endpoints (the AI model servers' policy):
    link-local and metadata addresses are always refused, private ones only when the deployment
    blocks private addresses. The address is checked on every connection.
    """

    def __init__(self, base_url: str, token: str, *, source: str, name: str) -> None:
        self.base_url = base_url.strip().rstrip("/")
        self._token = token
        self._source = source
        self._name = name

    def cooldown_key(self, role: str) -> str:
        """Failures are remembered per server, credential and role: one org's bad token, or a
        server that can't search, doesn't make the same server sit out for others."""
        return f"{role}:{self.base_url}:{fingerprint(self._token)}"

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    async def call(
        self,
        method: str,
        path: str,
        deadline: Deadline,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.base_url.startswith(("http://", "https://")):
            raise NotConfigured(
                f"{self._name} needs the http(s) address of its server", source=self._source, reason="base_url",
            )
        async with guarded_async_client(self.base_url, timeout=max(deadline.remaining(), 0.001)) as client:
            return await request_json(
                client, method, f"{self.base_url}{path}",
                source=self._source, name=self._name, deadline=deadline,
                headers=self.headers(), json=json, params=params,
            )
