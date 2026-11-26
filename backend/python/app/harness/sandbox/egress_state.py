"""The egress proxy's durable state on the deployment's Redis (P5.4b): ``EgressStateStore`` over ``RedisClient``.

Redis because the harness already depends on it (spend, request grants, the
live bus), the proxy already sits on its network (Compose ``sandbox_kv``), and a
binding needs a TTL. The client comes from ``harness_redis`` (the provider
factory); every key of one proxy shares a hash tag, so a cluster keeps them in
one slot. Values are sealed by ``harness_core.sandbox.egress.state`` before they
get here.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, cast

from app.harness.access.redis_client import harness_redis
from app.utils.encryption.component_key import component_identity
from app.utils.encryption.secret_key_ring import deployment_ring, secret_key_configured
from app.utils.env_flags import EGRESS_STATE_REQUIRED, env_flag
from harness_core.logs import HarnessLog
from harness_core.sandbox.egress.sealed import CredentialSealer
from harness_core.sandbox.egress.state import StateSealer

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from app.config.configuration_service import ConfigurationService
    from app.services.redis.connection_provider import RedisClient

ENV_SECRET_KEY = "SECRET_KEY"
ENV_STATE_URL = "AGENT_HARNESS_STATE_URL"
DEFAULT_SCOPE = "default"
_SCAN_COUNT = 500
# One server-side step, so a proxy's write between the read and the write is never overwritten; KEEPTTL (Redis 6+)
# keeps the record's expiry.
_REPLACE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  redis.call('SET', KEYS[1], ARGV[2], 'KEEPTTL')
  return 1
end
return 0
"""
_LOG = HarnessLog.named("app.harness.sandbox.egress_state")


class RedisEgressStateStore:
    def __init__(self, client: RedisClient, *, namespace: str = "", scope: str = DEFAULT_SCOPE) -> None:
        self._client = client
        self._prefix = f"{namespace}harness:egress:{{{scope}}}:"

    async def put(self, key: str, value: str, *, ttl_s: int | None) -> None:
        _ = await cast("Awaitable[object]", self._client.set(self._prefix + key, value, ex=ttl_s))  # pyright: ignore[reportUnknownMemberType]

    async def put_if_absent(self, key: str, value: str) -> str:
        full = self._prefix + key
        _ = await cast("Awaitable[object]", self._client.set(full, value, nx=True))  # pyright: ignore[reportUnknownMemberType]
        held = await cast("Awaitable[str | None]", self._client.get(full))  # pyright: ignore[reportUnknownMemberType]
        return held if held is not None else value

    async def delete(self, key: str) -> None:
        _ = await cast("Awaitable[int]", self._client.delete(self._prefix + key))  # pyright: ignore[reportUnknownMemberType]

    async def replace(self, key: str, expected: str, value: str) -> bool:
        replaced = await cast(
            "Awaitable[int]",
            self._client.eval(_REPLACE_SCRIPT, 1, self._prefix + key, expected, value),  # pyright: ignore[reportUnknownMemberType]
        )
        return replaced == 1

    async def entries(self) -> tuple[tuple[str, str], ...]:
        keys = [
            cast("str", key)
            async for key in self._client.scan_iter(match=self._prefix + "*", count=_SCAN_COUNT)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        ]
        if not keys:
            return ()
        values = await cast("Awaitable[list[str | None]]", self._client.mget(keys))  # pyright: ignore[reportUnknownMemberType]
        # A key that expired between the scan and the read is simply gone.
        return tuple(
            sorted((key[len(self._prefix) :], value) for key, value in zip(keys, values, strict=True) if value is not None)
        )


class EgressStateUnavailable(RuntimeError):
    """The proxy must keep durable state here and cannot: it refuses to start rather than run on bindings a
    restart would lose (``reason``: ``redis_unreachable`` or ``secret_key_missing``)."""

    reason_code = "egress_state_unavailable"

    def __init__(self, reason: str) -> None:
        super().__init__(f"the egress proxy's durable state is unavailable: {reason}")
        self.reason = reason


def state_required() -> bool:
    """``EGRESS_STATE_REQUIRED``; unset, it follows ``AGENT_HARNESS_STATE_URL``. Every deployment of the proxy
    (Compose, Helm, the fault stack) names Node's address there, while a from-source run relies on its localhost
    default, so the address is the deployment's own statement that this proxy is not a developer's."""
    return env_flag(EGRESS_STATE_REQUIRED, follows=bool(os.getenv(ENV_STATE_URL)))


def state_sealer() -> StateSealer | None:
    """The sealer keyed by the deployment's ``SECRET_KEY`` (the KV store's own key), opening records sealed under
    ``SECRET_KEY_PREVIOUS`` or ``SECRET_KEY_NEXT`` too (ADR-KEY-01); ``None`` when unset."""
    ring = deployment_ring()
    if ring is None:
        return None
    current, accepted = ring.raw()
    return StateSealer(current, accepted=accepted)


async def component_sealing_keys(config_service: ConfigurationService) -> tuple[tuple[bytes, ...], tuple[bytes, ...]]:
    """On the sandbox plane (ADR-KEY-02): the egress sealing keys Node derived from the ``SECRET_KEY`` ring and sealed
    for this proxy, state keys then credential keys, each the writer's first; empty until Node has published them."""
    from app.harness.credential_roles import ComponentCredentials

    identity = component_identity()
    if identity is None:
        return (), ()
    sealing = (await ComponentCredentials(config_service, identity[0]).document()).get("egressSealing")
    if not isinstance(sealing, dict):
        return (), ()

    def keys(name: str) -> tuple[bytes, ...]:
        values = sealing.get(name)
        return tuple(bytes.fromhex(v) for v in values if isinstance(v, str)) if isinstance(values, list) else ()

    return keys("state"), keys("credentials")


async def egress_sealers(config_service: ConfigurationService) -> tuple[StateSealer | None, CredentialSealer | None]:
    """The proxy's two sealers: from the derived keys Node sealed for it on the sandbox plane, else from the ring."""
    if component_identity() is not None:
        state, credentials = await component_sealing_keys(config_service)
        return (
            StateSealer.of_keys(state) if state else None,
            CredentialSealer.of_keys(credentials) if credentials else None,
        )
    from app.harness.sandbox.egress_credentials import credential_sealer

    return state_sealer(), credential_sealer()


async def redis_state_store(
    config_service: ConfigurationService, *, required: bool = False, keyed: bool | None = None
) -> RedisEgressStateStore | None:
    """The proxy's state store. When Redis or the key is missing: ``required`` raises ``EgressStateUnavailable``
    (logged once at error), otherwise ``None`` (logged once at warning) and state stays in memory. ``keyed``: whether
    a sealer exists (default: whether ``SECRET_KEY`` is set)."""
    if not (secret_key_configured() if keyed is None else keyed):
        reason = "secret_key_missing"
    else:
        redis = await harness_redis(config_service)
        if redis is not None:
            client, namespace = redis
            return RedisEgressStateStore(client, namespace=namespace)
        reason = "redis_unreachable"
    if required:
        _LOG.error("egress.state_required", reason=reason, choice="refuse_start")
        raise EgressStateUnavailable(reason)
    _LOG.warning("egress.state_unavailable", reason=reason)
    return None


async def durable_state(config_service: ConfigurationService) -> tuple[RedisEgressStateStore | None, StateSealer | None]:
    """What the proxy role starts with: its store and sealer, or neither (state in memory) where that is allowed."""
    sealer, _ = await egress_sealers(config_service)
    store = await redis_state_store(config_service, required=state_required(), keyed=sealer is not None)
    return store, sealer if store is not None else None


__all__ = [
    "DEFAULT_SCOPE",
    "ENV_SECRET_KEY",
    "ENV_STATE_URL",
    "EgressStateUnavailable",
    "RedisEgressStateStore",
    "component_sealing_keys",
    "durable_state",
    "egress_sealers",
    "redis_state_store",
    "state_required",
    "state_sealer",
]
