"""The process's MCP hub (P2.8): a schema cache and a pool of MCP sessions kept open across requests.

Sessions are pooled per (org, instance, credential owner); each is a connected
``MCPClientManager`` (an HTTP session, or a session with a server process in a P5.10
runner). Its variant is the instance's stored configuration and an HMAC of the
credential it was opened with, so an edited instance or a rotated token opens a new
session. Discovered tool lists are cached per (org, instance, owner, configuration);
a server's ``notifications/tools/list_changed`` drops its entries.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from mcp.client.subscriptions import ListenNotSupportedError
from mcp.types import ToolListChangedNotification

from app.agents.mcp.client import MCPClientManager, ToolListing
from app.agents.mcp.models import MCPTransport
from app.agents.mcp.tool_cache import stale_ttl_seconds
from app.core.org_context import spawn_detached
from app.harness.mcp_hub.breaker import ServerBreakers
from app.harness.mcp_hub.settings import hub_settings
from harness_core.clock import SystemClock
from harness_core.contracts.canonical_json import canonical_sha256
from harness_core.logs import HarnessLog
from harness_core.mcp_hub.annotations import declared_hints
from harness_core.mcp_hub.manifest import ToolDescriptor
from harness_core.mcp_hub.memory import InMemoryToolSchemaCache
from harness_core.mcp_hub.pool import SessionPool
from harness_core.mcp_hub.types import (
    CachedTools,
    PoolConfig,
    SchemaKey,
    SessionKey,
    list_ttl_s,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from mcp import Client

    from app.agents.mcp.models import MCPServerConfig
    from app.harness.mcp_hub.settings import HubSettings
    from app.harness.mcp_runner.transport import RunnerTarget
    from harness_core.clock import Clock
    from harness_core.mcp_hub.pool import SessionLease
    from harness_core.mcp_hub.ports import ToolSchemaCache

_LOG = HarnessLog.named("app.harness.mcp_hub")
# A pooled STDIO session's first open starts its runner (a sandbox), which the runner bounds by its own ready
# timeout; the instance's connect timeout is a network handshake's bound, too short for a cold start.
RUNNER_CONNECT_TIMEOUT_S = 60.0
_MAX_SWEEP_INTERVAL_S = 60.0


def config_digest(config: MCPServerConfig) -> str:
    """Any stored change to the instance (``updatedAt`` included) is a new digest."""
    return canonical_sha256(config.model_dump(mode="json", by_alias=True))


@dataclass(frozen=True)
class HubTarget:
    """One instance as one credential owner connects to it; ``env`` and ``headers`` hold the resolved secret."""

    config: MCPServerConfig
    key: SessionKey
    env: Mapping[str, str] = field(repr=False)
    headers: Mapping[str, str] = field(repr=False)
    runner: RunnerTarget | None = None

    @property
    def schema_key(self) -> SchemaKey:
        return SchemaKey(self.key.tenant, self.key.server_id, self.key.owner, config_digest(self.config))

    @property
    def pooled(self) -> bool:
        """HTTP sessions and sessions in P5.10 runners are pooled; a STDIO child of this process never outlives a request."""
        return self.config.transport != MCPTransport.STDIO or self.runner is not None


class _ListChanged:
    def __init__(self, hub: MCPHub, key: SessionKey) -> None:
        self._hub = hub
        self._key = key

    async def __call__(self, message: object) -> None:
        if isinstance(message, ToolListChangedNotification):
            await self._hub.tools_changed(self._key.tenant, self._key.server_id)


def _descriptor(tool: Any) -> ToolDescriptor:  # noqa: ANN401 - an SDK `Tool`
    return ToolDescriptor(
        tool.name, tool.description, dict(tool.input_schema or {}), hints=declared_hints(tool.annotations)
    )


def _listed(listing: ToolListing, client: Client | None) -> tuple[list[ToolDescriptor], object, Any, str | None]:
    """The listing's tools, its ``ttlMs`` (2026-07-28) if any, and the server's version and protocol."""
    ttl_s = listing.ttl_seconds
    if ttl_s == 0:
        # The 2026-07-28 default every server that set nothing sends; read as main's tool cache reads it.
        ttl_s = float(stale_ttl_seconds())
    ttl_ms = ttl_s * 1000 if ttl_s is not None else None
    info = client.server_info if client is not None else None
    protocol = client.protocol_version if client is not None else None
    return [_descriptor(t) for t in listing.tools], ttl_ms, info.version if info is not None else None, protocol


class MCPHub:
    def __init__(
        self,
        settings: HubSettings,
        *,
        clock: Clock | None = None,
        cache: ToolSchemaCache | None = None,
    ) -> None:
        self._clock = clock or SystemClock()
        self._settings = settings
        self._cache = cache or InMemoryToolSchemaCache(self._clock)
        self._pool: SessionPool[MCPClientManager] = SessionPool(
            clock=self._clock,
            close=self._close_manager,
            config=PoolConfig(idle_ttl_s=settings.session_idle_ttl_s, max_age_s=settings.session_max_age_s),
            log=_LOG,
        )
        self._breakers = ServerBreakers(failures=settings.breaker_failures, cooldown_s=settings.breaker_cooldown_s)
        self._fingerprint_key = secrets.token_bytes(32)
        self._sweeper: asyncio.Task[None] | None = None
        self._listeners: dict[int, asyncio.Task[None]] = {}

    @property
    def pool(self) -> SessionPool[MCPClientManager]:
        return self._pool

    @property
    def breakers(self) -> ServerBreakers:
        return self._breakers

    def _variant(self, target: HubTarget) -> str:
        mac = hmac.new(self._fingerprint_key, digestmod=hashlib.sha256)
        for kind, values in (("env", target.env), ("header", target.headers)):
            for name, value in sorted(values.items()):
                mac.update(f"{kind}\x00{name}\x00{value}\x00".encode())
        return f"{config_digest(target.config)}:{mac.hexdigest()}"

    async def lease(self, target: HubTarget) -> SessionLease[MCPClientManager]:
        if not target.pooled:
            raise ValueError(f"MCP instance {target.key.server_id} runs as a child of this process and is not pooled")
        self._start_sweeper()

        async def opener() -> MCPClientManager:
            manager = MCPClientManager(
                target.config,
                env=dict(target.env),
                headers=dict(target.headers),
                runner=target.runner,
                message_handler=_ListChanged(self, target.key),
                connect_timeout_seconds=RUNNER_CONNECT_TIMEOUT_S if target.runner is not None else None,
            )
            client = await manager.open()
            # Detached: the pooled client outlives the request that opened it and serves every later one.
            self._listeners[id(manager)] = spawn_detached(self._listen(client, target.key))
            return manager

        return await self._pool.acquire(target.key, self._variant(target), opener)

    async def cached(self, target: HubTarget) -> CachedTools | None:
        """The owner's fresh cached list of the server's tools, if any; the server is not asked."""
        return await self._cache.get(target.schema_key)

    async def discover(self, target: HubTarget, *, refresh: bool = False) -> CachedTools:
        """The server's tools as this owner sees them: cached while fresh, else listed over a pooled session."""
        schema_key = target.schema_key
        if not refresh and (cached := await self._cache.get(schema_key)) is not None:
            return cached
        if target.pooled:
            lease = await self.lease(target)
            try:
                listing = await lease.session.fetch_tool_listing_in_session()
                tools, ttl_ms, version, protocol = _listed(listing, lease.session.session_client)
            except BaseException:
                await lease.discard()
                raise
            await lease.release()
        else:
            manager = MCPClientManager(target.config, env=dict(target.env), headers=dict(target.headers))
            try:
                _ = await manager.open()
                listing = await manager.fetch_tool_listing_in_session()
                tools, ttl_ms, version, protocol = _listed(listing, manager.session_client)
            finally:
                await manager.aclose()
        entry = CachedTools(
            tools=tuple(tools),
            server_version=version,
            protocol_version=protocol,
            ttl_s=list_ttl_s(ttl_ms, float(self._settings.schema_ttl_s)),
        )
        await self._cache.put(schema_key, entry)
        return entry

    async def _listen(self, client: Client, key: SessionKey) -> None:
        """A 2026-07-28 server says its tools changed only on a ``subscriptions/listen`` stream; the SDK then also
        hands the notification to the session's message handler (``_ListChanged``). Older servers send it unasked."""
        try:
            async with client.listen(tools_list_changed=True) as subscription:
                async for _ in subscription:
                    pass
        except ListenNotSupportedError:
            return
        except Exception as exc:
            # The cache still expires by its TTL; a lost stream only delays a change.
            _LOG.info("mcp_hub.listen_ended", orgId=key.tenant, mcpServer=key.server_id, errorType=type(exc).__name__)

    async def _close_manager(self, manager: MCPClientManager) -> None:
        if (listener := self._listeners.pop(id(manager), None)) is not None:
            _ = listener.cancel()
        await manager.aclose()

    async def tools_changed(self, tenant: str, server_id: str) -> None:
        dropped = await self._cache.invalidate(tenant, server_id)
        _LOG.info("mcp_hub.tools_changed", orgId=tenant, mcpServer=server_id, dropped=dropped)

    async def sweep(self) -> int:
        return await self._pool.sweep()

    def _start_sweeper(self) -> None:
        if self._sweeper is None or self._sweeper.done():
            interval = min(_MAX_SWEEP_INTERVAL_S, self._settings.session_idle_ttl_s / 4)
            self._sweeper = spawn_detached(self._sweep_forever(interval))

    async def _sweep_forever(self, interval_s: float) -> None:
        while True:
            await asyncio.sleep(interval_s)
            try:
                _ = await self._pool.sweep()
            except Exception as exc:
                _LOG.error("mcp_hub.sweep_failed", exc=exc, errorType=type(exc).__name__)

    async def aclose(self) -> int:
        if self._sweeper is not None:
            _ = self._sweeper.cancel()
            self._sweeper = None
        return await self._pool.close_all()


class ProcessMCPHub:
    """The process's hub, built on first use with the settings then."""

    def __init__(self) -> None:
        self._hub: MCPHub | None = None

    def get(self) -> MCPHub:
        if self._hub is None:
            settings = hub_settings()
            self._hub = MCPHub(settings)
            _LOG.info(
                "mcp_hub.started",
                sessionIdleTtlS=settings.session_idle_ttl_s,
                sessionMaxAgeS=settings.session_max_age_s,
                schemaTtlS=settings.schema_ttl_s,
                breakerFailures=settings.breaker_failures,
                breakerCooldownS=settings.breaker_cooldown_s,
            )
        return self._hub

    async def aclose(self) -> int:
        hub, self._hub = self._hub, None
        return await hub.aclose() if hub is not None else 0


process_mcp_hub = ProcessMCPHub()

__all__ = ["HubTarget", "MCPHub", "ProcessMCPHub", "config_digest", "process_mcp_hub"]
