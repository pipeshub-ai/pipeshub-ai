"""CPD-7's dual write, query side: after an agent or a toolset or MCP instance changed, ask Node to sync it.

The graph edges and KV instances stay the store of record for this release; the control plane's installations
and grants follow them. Node reads the facts back through the legacy driver and applies the migration's plan,
so what is sent is only *what* changed, never the facts. A failed sync never fails the write it follows: it is
logged once at warning with its reason code, the shadow counts the difference, and the migration's next apply
repairs it (idempotent). An org whose harness is off (ADR-OPS-08) is not synced at all: its writes reach Node
as a release without the harness makes them, and nothing is logged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

import httpx

from app.api.middlewares.caller_role import nodejs_endpoint
from app.config.constants.service import TokenScopes, config_node_constants
from app.services.featureflag.platform_settings import is_agent_harness_on
from app.utils.jwt import mint_service_token
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService

SYNC_PATH = "/api/v1/control-plane/internal/capabilities/sync"
_TIMEOUT_SECONDS = 5.0
_log = HarnessLog.named("app.agents.capability_sync")


class SyncTransport(Protocol):
    async def post(self, url: str, *, json: dict[str, object], headers: dict[str, str]) -> int: ...


class _Httpx:
    async def post(self, url: str, *, json: dict[str, object], headers: dict[str, str]) -> int:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            return (await client.post(url, json=json, headers=headers)).status_code


class _Config(Protocol):
    async def get_config(self, key: str, default: object = None, use_cache: bool = False) -> object: ...  # noqa: FBT001, FBT002 - ConfigurationService's signature


async def _token(config_service: ConfigurationService, org_id: str) -> str | None:
    keys = await cast("_Config", config_service).get_config(config_node_constants.SECRET_KEYS.value, use_cache=True)
    secret = cast("dict[str, object]", keys).get("scopedJwtSecret") if isinstance(keys, dict) else None
    if not isinstance(secret, str) or not secret:
        return None
    return mint_service_token(secret, {"orgId": org_id, "scopes": [TokenScopes.CAPABILITIES_SYNC.value]})


async def sync_capabilities(
    config_service: ConfigurationService,
    org_id: str,
    *,
    agent_key: str | None = None,
    transport: SyncTransport | None = None,
) -> bool:
    """Ask Node to sync one agent's grant (``agent_key``) or the org's instances; True when Node applied it."""
    if not await is_agent_harness_on(config_service):
        return False
    body: dict[str, object] = {"agentKey": agent_key} if agent_key is not None else {"installations": True}
    try:
        token = await _token(config_service, org_id)
        if token is None:
            _log.warning("control_plane.sync_failed", orgId=org_id, reasonCode="service_secret_unavailable")
            return False
        url = f"{await nodejs_endpoint(config_service)}{SYNC_PATH}"
        status = await (transport or _Httpx()).post(url, json=body, headers={"Authorization": f"Bearer {token}"})
    except Exception as exc:
        _log.warning("control_plane.sync_failed", orgId=org_id, reasonCode="control_plane_unreachable",
                     errorType=type(exc).__name__)
        return False
    if status != 200:
        _log.warning("control_plane.sync_failed", orgId=org_id, reasonCode="control_plane_refused", status=status)
        return False
    return True


class CapabilitySync:
    """The notifier the agent routes take from their services (the toolset and MCP routes call ``sync_capabilities``)."""

    def __init__(self, config_service: ConfigurationService, transport: SyncTransport | None = None) -> None:
        self._config = config_service
        self._transport = transport

    async def agent(self, org_id: str, agent_key: str) -> bool:
        return await sync_capabilities(self._config, org_id, agent_key=agent_key, transport=self._transport)


__all__ = ["SYNC_PATH", "CapabilitySync", "SyncTransport", "sync_capabilities"]
