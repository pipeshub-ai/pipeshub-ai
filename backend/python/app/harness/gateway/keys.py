"""``GatewayKeySource`` over configuration: the public half of Node's gateway-token keyring.

Node writes ``/services/agentHarness/gatewayPublicKeys`` (encrypted like every
harness config document) whenever it provisions or rotates; this reads it
through ``ConfigurationService``, keeps it for a short TTL, and re-reads on a
refresh (an unknown ``kid``).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, cast

from app.config.constants.service import config_node_constants
from harness_core.gateway.ports import PublishedKey, PublishedKeys

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.config.configuration_service import ConfigurationService

CACHE_TTL_S = 30.0


def parse_published(document: object) -> PublishedKeys | None:
    """Node's ``PublishedSigningKeys`` document; ``None`` when absent or not that shape."""
    if not isinstance(document, dict):
        return None
    raw = cast("dict[str, object]", document)
    issuer, audience, keys = raw.get("issuer"), raw.get("audience"), raw.get("keys")
    if not isinstance(issuer, str) or not isinstance(audience, str) or not isinstance(keys, list):
        return None
    parsed: list[PublishedKey] = []
    for item in cast("list[object]", keys):
        if not isinstance(item, dict):
            continue
        key = cast("dict[str, object]", item)
        kid, pem, retired = key.get("kid"), key.get("publicKey"), key.get("retiredAt")
        if key.get("alg") != "EdDSA" or not isinstance(kid, str) or not isinstance(pem, str):
            continue
        parsed.append(PublishedKey(kid, pem, int(retired) if isinstance(retired, int | float) else None))
    return PublishedKeys(issuer, audience, tuple(parsed))


class ConfiguredGatewayKeys:
    def __init__(
        self,
        config_service: ConfigurationService,
        *,
        ttl_s: float = CACHE_TTL_S,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config_service = config_service
        self._ttl_s = ttl_s
        self._monotonic = monotonic
        self._cached: PublishedKeys | None = None
        self._read_at: float | None = None

    async def keys(self, *, refresh: bool = False) -> PublishedKeys | None:
        now = self._monotonic()
        if not refresh and self._read_at is not None and now - self._read_at < self._ttl_s:
            return self._cached
        # Absent and unreadable both mean no known key: every token is refused until a read succeeds.
        document: object = await self._config_service.get_config(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            config_node_constants.AGENT_HARNESS_GATEWAY_PUBLIC_KEYS.value, raise_on_error=False
        )
        self._cached, self._read_at = parse_published(cast("object", document)), now
        return self._cached


__all__ = ["ConfiguredGatewayKeys", "parse_published"]
