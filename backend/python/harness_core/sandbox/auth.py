"""How a broker client authenticates and how a broker server checks it.

Two small ports so each side depends only on what it uses; an embedding
application binds its own credential scheme (PipesHub binds the worker
credential in ``app/harness/sandbox/auth.py``).
"""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping

AUTHORIZATION = "Authorization"


class BrokerAuth(Protocol):
    """Client side: headers to send with every broker request."""

    async def headers(self) -> Mapping[str, str]: ...


class BrokerAuthVerifier(Protocol):
    """Server side: whether a request's headers carry a credential accepted on ``path`` (an embedding may admit
    different callers on different routes: PipesHub admits only its scraper's credential on ``/metrics``)."""

    async def verify(self, headers: Mapping[str, str], path: str = "") -> bool: ...


def bearer(headers: Mapping[str, str]) -> str | None:
    for key, value in headers.items():
        if key.lower() == AUTHORIZATION.lower():
            scheme, _, token = value.partition(" ")
            if scheme.lower() != "bearer":
                return None
            return token.strip() or None
    return None


class StaticBearerAuth:
    """One shared bearer token, both sides. For tests and single-tenant embeddings."""

    def __init__(self, token: str) -> None:
        if not token:
            raise ValueError("token is empty")
        self._token = token

    async def headers(self) -> Mapping[str, str]:
        return {AUTHORIZATION: f"Bearer {self._token}"}

    async def verify(self, headers: Mapping[str, str], path: str = "") -> bool:
        del path
        presented = bearer(headers)
        return presented is not None and hmac.compare_digest(
            presented.encode("utf-8"), self._token.encode("utf-8")
        )


__all__ = ["AUTHORIZATION", "BrokerAuth", "BrokerAuthVerifier", "StaticBearerAuth", "bearer"]
