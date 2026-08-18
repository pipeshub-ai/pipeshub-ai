"""`MCPSessionManager` — per-request, per-instance long-lived MCP client cache.

Mirrors `ToolInstanceCreator`'s `_client_cache` (`instance_creator.py`): one open
`MCPClientManager` session per instance is cached on `context.tool_state` so every
`MCPToolAdapter` execution against the same instance within a single chat turn reuses ONE
connection (`MCPClientManager.open()`/`call_tool_in_session()`) instead of reconnecting per
tool call. Phase 1's one-shot discovery (`discovery.discover_tools()`) is unaffected — it
still uses `MCPClientManager.connect()`'s short-lived, per-call contract.

On a failed call whose error looks like an expired/invalid OAuth token, this refreshes the
credential once (`app.agents.mcp.token_refresh.refresh_credential_record`), rebuilds the
session with the fresh token, and retries the SAME call exactly once before letting the
error propagate — the caller (`MCPToolAdapter.execute()`) turns that into a
`tool_unavailable` SSE event, never a second retry loop.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from app.agents.mcp.client import MCPClientManager
from app.agents.mcp.discovery import build_auth_env_and_headers
from app.agents.mcp.models import MCPAuthMode
from app.agents.mcp.service import credentials_to_discovery_dict, instance_config_from_dict
from app.agents.mcp.token_refresh import refresh_credential_record

if TYPE_CHECKING:
    from app.agents.agent_loop.context import AgentContext
    from app.agents.agent_loop.mcp_access import ResolvedMCPServer

logger = logging.getLogger(__name__)

__all__ = ["MCPSessionManager"]

# Message-substring heuristic for "this call failed because the access token
# expired or was revoked" — same style as `tool_loader.py`'s
# `_AUTH_ERROR_MARKERS` (a typed exception isn't available here: MCP
# transports/SDKs surface expiry as an HTTP 401 or a JSON-RPC error whose
# message contains one of these, not a dedicated exception this codebase
# controls).
_EXPIRY_MARKERS = ("401", "unauthorized", "invalid_token", "invalid token", "token expired", "expired")


def _looks_like_expired_token(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _EXPIRY_MARKERS)


class MCPSessionManager:
    """Cached on `context.tool_state` — construct freely per call site
    (`MCPToolProvider.load_into`, `stream_bridge.py`'s teardown); every
    instance sharing the same `tool_state` dict sees the same underlying
    session cache."""

    def __init__(self, context: "AgentContext") -> None:
        self._context = context
        self._log = context.logger or logger
        state = context.tool_state
        state.setdefault("_mcp_client_managers", {})
        state.setdefault("_mcp_client_locks", {})
        self._managers: dict[str, MCPClientManager] = state["_mcp_client_managers"]
        self._locks: dict[str, asyncio.Lock] = state["_mcp_client_locks"]

    async def call(self, server: "ResolvedMCPServer", tool_name: str, arguments: dict[str, Any]) -> Any:
        manager = await self._get_or_open(server)
        try:
            return await manager.call_tool_in_session(tool_name, arguments)
        except Exception as exc:
            auth_mode = server.instance.get("authMode")
            if auth_mode != MCPAuthMode.OAUTH.value or not _looks_like_expired_token(exc):
                raise
            self._log.info(
                "MCPSessionManager: call to MCP instance %s looked like an expired token, "
                "refreshing once and retrying", server.instance_id,
            )
            manager = await self._refresh_and_reopen_locked(server, stale_manager=manager)
            return await manager.call_tool_in_session(tool_name, arguments)

    async def aclose_all(self) -> None:
        """Tears down every session opened this request — called from
        `stream_bridge.py`'s existing `finally` block."""
        for instance_id, manager in list(self._managers.items()):
            try:
                await manager.aclose()
            except Exception as exc:
                self._log.debug("MCPSessionManager: error closing session for %s: %s", instance_id, exc)
        self._managers.clear()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _get_or_open(self, server: "ResolvedMCPServer") -> MCPClientManager:
        manager = self._managers.get(server.instance_id)
        if manager is not None:
            return manager
        lock = self._locks.setdefault(server.instance_id, asyncio.Lock())
        async with lock:
            manager = self._managers.get(server.instance_id)
            if manager is not None:
                return manager
            manager = await self._build_and_open(server)
            self._managers[server.instance_id] = manager
            return manager

    async def _build_and_open(self, server: "ResolvedMCPServer") -> MCPClientManager:
        config = instance_config_from_dict(server.instance)
        credentials = credentials_to_discovery_dict(server.instance.get("authMode", ""), server.auth)
        env, headers = build_auth_env_and_headers(config, credentials)
        manager = MCPClientManager(config, env=env, headers=headers)
        await manager.open()
        return manager

    async def _refresh_and_reopen_locked(
        self, server: "ResolvedMCPServer", *, stale_manager: MCPClientManager,
    ) -> MCPClientManager:
        """Serializes `_refresh_and_reopen` per instance — without this, two concurrent tool
        calls hitting the same expired-token instance would both pop/close the cached
        manager and both rebuild+store a new one, leaking whichever session loses the final
        `self._managers[...] =` write (an orphaned STDIO subprocess or open connection) and
        double-refreshing the credential. `refresh_credential_record` itself is also
        lock-guarded (per instance+owner, shared across every caller of that function — see
        `token_refresh.py`), but that alone doesn't stop two callers here from each rebuilding
        and storing their own session afterward.

        `stale_manager` is the manager identity that triggered this refresh — if another
        task already refreshed-and-reopened while this one was waiting for the lock, the
        session cache entry will have moved on to a different manager object, and this call
        reuses that one instead of refreshing (and reopening) all over again.
        """
        lock = self._locks.setdefault(server.instance_id, asyncio.Lock())
        async with lock:
            current = self._managers.get(server.instance_id)
            if current is not None and current is not stale_manager:
                return current
            await self._refresh_and_reopen(server)
            return self._managers[server.instance_id]

    async def _refresh_and_reopen(self, server: "ResolvedMCPServer") -> None:
        old_manager = self._managers.pop(server.instance_id, None)
        if old_manager is not None:
            await old_manager.aclose()

        new_tokens = await refresh_credential_record(
            server.instance_id, server.owner_id, self._context.config_service,
        )
        # Mutated in place (not re-fetched from `mcp_server_configs`) so every
        # `MCPToolAdapter` sharing this `ResolvedMCPServer` sees the fresh
        # token for the rest of this request.
        server.auth = {
            "isAuthenticated": True,
            "oauthTokens": new_tokens.model_dump(by_alias=True, mode="json"),
        }
        manager = await self._build_and_open(server)
        self._managers[server.instance_id] = manager
