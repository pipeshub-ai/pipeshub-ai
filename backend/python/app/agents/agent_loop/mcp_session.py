"""`MCPSessionManager` — per-request, per-instance long-lived MCP client cache.

Mirrors `ToolInstanceCreator`'s `_client_cache` (`instance_creator.py`): one open
`MCPClientManager` session per instance is cached on `context.tool_state`, and a chat turn's
tool discovery (`discover()`) and every `MCPToolAdapter` call against that instance use it —
one connection (one STDIO process) per server per turn instead of one for discovery and
another for the calls. A session whose connection closed (a local server that exited) or that
the server has forgotten (an expired session id) is replaced on its next use.

A call is sent again only when it can't have run: it never reached the server, the server
refused its session, or the server answered it with HTTP 401 (after a token refresh). One whose
connection failed after it went out may have run, so it is reported, never repeated. Every
failure is the call's own: the client reads it from that call's requests alone.

When an OAuth instance's listing or call is rejected with HTTP 401, this refreshes the
credential once (`app.agents.mcp.token_refresh.refresh_credential_record`) and retries once with
the fresh token: on the same session when it is still open, on a new one otherwise.

A turn takes a server's tools from the tool cache when it has them (`tools()`), and then opens
no session until the model calls one. Before that first call the session lists once, which
checks the cache against the server and gives the SDK the tools' schemas it would otherwise
list for itself.
"""
from __future__ import annotations

import asyncio
import functools
import logging
from contextlib import suppress
from typing import TYPE_CHECKING, Any, TypeVar

from app.agents.mcp import step_up, tool_cache
from app.agents.mcp.client import MCPClientManager, ToolListing
from app.agents.mcp.discovery import build_auth_env_and_headers, tool_infos_from_listing
from app.agents.mcp.errors import (
    MCPCallInterruptedError,
    MCPInsufficientScopeError,
    MCPToolChangedError,
    MCPToolNotOfferedError,
    is_http_unauthorized,
    request_may_have_run,
    request_never_reached_server,
)
from app.agents.mcp.models import MCPAuthMode, MCPToolInfo
from app.agents.mcp.service import (
    credentials_to_discovery_dict,
    instance_config_from_dict,
)
from app.agents.mcp.token_refresh import refresh_credential_record

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from mcp.shared.dispatcher import ProgressFnT

    from app.agents.agent_loop.context import AgentContext
    from app.agents.agent_loop.mcp_access import ResolvedMCPServer

logger = logging.getLogger(__name__)

__all__ = ["MCPSessionManager", "is_http_unauthorized"]

_Result = TypeVar("_Result")


def _asks_for_more_scopes(
    method: "Callable[..., Awaitable[_Result]]",
) -> "Callable[..., Awaitable[_Result]]":
    """A 403 `insufficient_scope` anywhere in `method` (connecting, after a token refresh, in a
    retry) is remembered for the next sign-in, and raised as what fixes it."""

    @functools.wraps(method)
    async def wrapper(self: "MCPSessionManager", server: "ResolvedMCPServer", *args: Any, **kwargs: Any) -> _Result:  # noqa: ANN401
        try:
            return await method(self, server, *args, **kwargs)
        except Exception as exc:
            needed = await self._needs_more_scopes(server, exc)
            if needed is None:
                raise
            raise needed from exc

    return wrapper


class MCPSessionManager:
    """Cached on `context.tool_state` — construct freely per call site
    (`MCPToolProvider.load_into`, `stream_bridge.py`'s teardown); every
    instance sharing the same `tool_state` dict sees the same underlying
    session cache."""

    def __init__(self, context: "AgentContext") -> None:
        self._context = context
        self._log = context.logger or logger
        state = context.tool_state
        self._managers: dict[str, MCPClientManager] = state.setdefault("_mcp_client_managers", {})
        self._locks: dict[str, asyncio.Lock] = state.setdefault("_mcp_client_locks", {})
        # Per instance: the cache entry this turn's tools came from, and the listing that checks
        # it before the first call.
        self._cache_hits: dict[str, tool_cache.CacheHit] = state.setdefault("_mcp_tool_cache_hits", {})
        self._first_listings: dict[str, asyncio.Future[dict[str, MCPToolInfo]]] = state.setdefault("_mcp_first_listings", {})
        # Per instance: the cache pointer this turn read or wrote, forgotten when the server
        # says its tools changed.
        self._cache_keys: dict[str, str] = state.setdefault("_mcp_tool_cache_keys", {})

    async def tools(self, server: "ResolvedMCPServer", namespace: str) -> tuple[list[MCPToolInfo], str | None]:
        """The server's tools and instructions for this turn: from the tool cache when it has
        them, opening no session, or listed on the session the calls will use and remembered."""
        config = instance_config_from_dict(server.instance)
        hit = await tool_cache.read(
            self._context.config_service, instance=server.instance, config=config,
            owner_id=server.owner_id, auth=server.auth,
        )
        if hit is not None:
            self._cache_hits[server.instance_id] = hit
            self._cache_keys[server.instance_id] = hit.owner_key
            listed = tool_infos_from_listing(tool_cache.tool_listing(hit.catalog), config, namespace)
            return listed, hit.catalog.instructions
        listing = await self.discover_listing(server)
        listed = tool_infos_from_listing(listing.tools, config, namespace)
        await self._remember(server, listing, listed)
        return listed, listing.instructions

    @_asks_for_more_scopes
    async def discover(self, server: "ResolvedMCPServer", namespace: str) -> list[MCPToolInfo]:
        """The server's tools, listed on the session this turn's calls will use."""
        manager = await self._get_or_open(server)
        return tool_infos_from_listing((await self.discover_listing(server)).tools, manager.config, namespace)

    @_asks_for_more_scopes
    async def discover_listing(self, server: "ResolvedMCPServer") -> ToolListing:
        """The server's listing on the session this turn's calls will use."""
        manager: MCPClientManager | None = None
        token = self._access_token(server)
        try:
            manager = await self._get_or_open(server)
            return await manager.fetch_tool_listing_in_session()
        except Exception as exc:
            failure = exc
        # Retried outside the `except`: a retry failing there would carry the first error as its context.
        if manager is not None and (request_never_reached_server(failure) or request_may_have_run(failure)):
            # Listing changes nothing on the server, so it can always be repeated.
            self._log.info("MCPSessionManager: listing MCP instance %s failed on the connection, trying again", server.instance_id)
            manager = await self._get_or_open(server)
        elif self._should_refresh(server, failure):
            self._log.info(
                "MCPSessionManager: MCP instance %s rejected discovery with HTTP 401, "
                "refreshing the token once and retrying", server.instance_id,
            )
            manager = await self._refreshed(server, token)
        else:
            raise failure
        return await manager.fetch_tool_listing_in_session()

    async def _remember(self, server: "ResolvedMCPServer", listing: ToolListing, listed: list[MCPToolInfo]) -> None:
        pointer = await tool_cache.write(
            self._context.config_service, instance=server.instance, config=instance_config_from_dict(server.instance),
            owner_id=server.owner_id, auth=server.auth,
            catalog=tool_cache.catalog_from_tools(listed, listing.instructions),
            server_ttl_seconds=listing.ttl_seconds, public=listing.public,
        )
        if pointer is not None:
            public = listing.public and tool_cache.public_allowed(server.instance)
            self._cache_keys[server.instance_id] = (
                tool_cache.PUBLIC_OWNER if public else tool_cache.owner_key(server.instance, server.owner_id)
            )

    @_asks_for_more_scopes
    async def call(
        self, server: "ResolvedMCPServer", tool_name: str, arguments: dict[str, Any],
        *, on_progress: "ProgressFnT | None" = None,
    ) -> Any:  # noqa: ANN401
        """Run one tool call. It is sent a second time only when the first can't have run: it
        never reached the server, the server refused its session, or it was refused with HTTP
        401 (sent again after a token refresh). `on_progress` hears the server's progress."""
        if server.instance_id in self._cache_hits:
            await self._listed_before_first_call(server, tool_name)
        token = self._access_token(server)
        try:
            manager = await self._get_or_open(server)
        except Exception as exc:
            if not self._should_refresh(server, exc):
                raise
            # The connection was refused, so the call was never sent.
            self._log.info("MCPSessionManager: MCP instance %s refused the connection with HTTP 401, refreshing the token", server.instance_id)
            manager = await self._refreshed(server, token)
            return await manager.call_tool_in_session(tool_name, arguments, on_progress=on_progress)
        try:
            return await manager.call_tool_in_session(tool_name, arguments, on_progress=on_progress)
        except Exception as exc:
            failure = exc
        # Handled outside the `except`: a retry failing there would carry the first error as its context.
        if request_never_reached_server(failure):
            self._log.info(
                "MCPSessionManager: a call to MCP instance %s never reached it (%s), sending it again",
                server.instance_id, type(failure).__name__,
            )
            manager = await self._get_or_open(server)
            return await manager.call_tool_in_session(tool_name, arguments, on_progress=on_progress)
        if request_may_have_run(failure):
            if not manager.is_open:
                await self._evict(server, manager)
            raise MCPCallInterruptedError(
                f"The connection to {server.display_name} failed while this call was running, so it may or "
                "may not have completed. Check its effect before running it again."
            ) from failure
        if not self._should_refresh(server, failure):
            raise failure
        self._log.info(
            "MCPSessionManager: MCP instance %s rejected the call with HTTP 401, "
            "refreshing the token once and retrying", server.instance_id,
        )
        manager = await self._refreshed(server, token)
        return await manager.call_tool_in_session(tool_name, arguments, on_progress=on_progress)

    async def _needs_more_scopes(self, server: "ResolvedMCPServer", exc: BaseException) -> MCPInsufficientScopeError | None:
        if isinstance(exc, MCPInsufficientScopeError):
            return None
        scopes = await step_up.remember_needed_scopes(self._context.config_service, server.instance, server.owner_id, exc)
        if not scopes:
            return None
        self._log.info("MCPSessionManager: MCP instance %s needs more scopes: %s", server.instance_id, scopes)
        # A service-account agent's sign-in is the agent's, made in its builder.
        where = "Workspace → MCP Servers" if server.owner_id == self._context.user_id else "this agent's settings in the agent builder"
        return MCPInsufficientScopeError(
            step_up.needs_more_scopes_message(server.display_name, scopes, reconnect_in=where), scopes=scopes,
        )

    async def _listed_before_first_call(self, server: "ResolvedMCPServer", tool_name: str) -> None:
        """On a turn whose tools came from the cache, the server lists once before its first
        call. Parallel first calls share that listing; it runs outside the instance lock,
        which the token refresh it may need takes too.

        When the listing fails, the call fails with its error if no session is left, and goes
        ahead unchecked otherwise: the listing wasn't the call, so it never ran. A failed
        listing is tried again by the next call."""
        listing = self._first_listings.get(server.instance_id)
        if listing is None:
            listing = self._first_listings[server.instance_id] = asyncio.ensure_future(self._check_the_cache(server))
        try:
            # Shielded: Stop cancels its caller, not the listing other calls are waiting for.
            offered = await asyncio.shield(listing)
        except Exception as exc:
            if self._first_listings.get(server.instance_id) is listing:
                del self._first_listings[server.instance_id]
            manager = self._managers.get(server.instance_id)
            if manager is None or not manager.is_open:
                raise
            self._log.info(
                "MCPSessionManager: listing MCP instance %s before a call failed (%s); calling anyway",
                server.instance_id, type(exc).__name__,
            )
            return
        if tool_name not in offered:
            raise MCPToolNotOfferedError(f"The {server.display_name} MCP server no longer offers the tool {tool_name}.")
        hit = self._cache_hits.get(server.instance_id)
        cached = next((tool for tool in hit.catalog.tools if tool.name == tool_name), None) if hit else None
        live = offered[tool_name]
        if cached is not None and (
            cached.input_schema != live.input_schema or (cached.annotations or None) != (live.annotations or None)
        ):
            raise MCPToolChangedError(
                f"The {server.display_name} MCP server changed the tool {tool_name} since this chat loaded it, "
                "so it wasn't called. Its new version is used from the next message."
            )

    async def _check_the_cache(self, server: "ResolvedMCPServer") -> dict[str, MCPToolInfo]:
        """Lists on the session and updates the cache entry this turn read: rewritten when the
        tools changed, refreshed when past half its life. Returns the tools offered, by name."""
        listing = await self.discover_listing(server)
        listed = tool_infos_from_listing(listing.tools, instance_config_from_dict(server.instance))
        hit = self._cache_hits.get(server.instance_id)
        catalog = tool_cache.catalog_from_tools(listed, listing.instructions)
        if hit is not None and (tool_cache.digest(catalog) != hit.pointer.digest or tool_cache.needs_refresh(hit.pointer)):
            await self._remember(server, listing, listed)
        return {tool.name: tool for tool in listed}

    async def _evict(self, server: "ResolvedMCPServer", manager: MCPClientManager) -> None:
        """Drops a session that can't carry another request, so the next use reconnects."""
        lock = self._locks.setdefault(server.instance_id, asyncio.Lock())
        async with lock:
            if self._managers.get(server.instance_id) is manager:
                del self._managers[server.instance_id]
        with suppress(Exception):
            await manager.aclose()

    @staticmethod
    def _should_refresh(server: "ResolvedMCPServer", exc: BaseException) -> bool:
        return server.instance.get("authMode") == MCPAuthMode.OAUTH.value and is_http_unauthorized(exc)

    @staticmethod
    def _access_token(server: "ResolvedMCPServer") -> str | None:
        if server.instance.get("authMode") != MCPAuthMode.OAUTH.value:
            return None
        return credentials_to_discovery_dict(MCPAuthMode.OAUTH.value, server.auth).get("accessToken")

    async def aclose_all(self) -> None:
        """Tears down every session opened this request — called from
        `stream_bridge.py`'s existing `finally` block. All at once: a hung server can take
        its close timeout."""
        managers = list(self._managers.items())
        self._managers.clear()
        for listing in self._first_listings.values():
            listing.cancel()
        self._first_listings.clear()
        for instance_id, manager in managers:
            # The server said its tools changed during the turn: the next turn lists again.
            if manager.tools_changed and (key := self._cache_keys.get(instance_id)):
                await tool_cache.forget(self._context.config_service, instance_id=instance_id, owner_key=key)
        results = await asyncio.gather(*(manager.aclose() for _, manager in managers), return_exceptions=True)
        for (instance_id, _), result in zip(managers, results):
            if isinstance(result, Exception):
                self._log.debug("MCPSessionManager: error closing session for %s: %s", instance_id, result)

    async def _get_or_open(self, server: "ResolvedMCPServer") -> MCPClientManager:
        manager = self._managers.get(server.instance_id)
        if manager is not None and manager.is_open:
            return manager
        lock = self._locks.setdefault(server.instance_id, asyncio.Lock())
        async with lock:
            manager = self._managers.get(server.instance_id)
            if manager is not None and manager.is_open:
                return manager
            if manager is not None:
                self._log.info("MCPSessionManager: session for MCP instance %s ended, reconnecting", server.instance_id)
                del self._managers[server.instance_id]
                await manager.aclose()
            manager = await self._build_and_open(server)
            self._managers[server.instance_id] = manager
            return manager

    async def _build_and_open(self, server: "ResolvedMCPServer") -> MCPClientManager:
        config = instance_config_from_dict(server.instance)
        env, headers = build_auth_env_and_headers(config, credentials_to_discovery_dict(server.instance.get("authMode", ""), server.auth))
        manager = MCPClientManager(config, env=env, headers=headers)
        await manager.open()
        return manager

    async def _refreshed(self, server: "ResolvedMCPServer", rejected_token: str | None) -> MCPClientManager:
        """A session sending a fresh token: the open one with its headers swapped, or a new one.

        `rejected_token` is the token the failed request carried. Concurrent 401s share one
        refresh: `refresh_credential_record` returns the stored tokens without calling the
        endpoint again when they already differ from it. The per-instance lock keeps two callers
        from each opening, and leaking, a session of their own."""
        lock = self._locks.setdefault(server.instance_id, asyncio.Lock())
        async with lock:
            tokens = await refresh_credential_record(
                server.instance_id, server.owner_id, self._context.config_service, stale_access_token=rejected_token,
            )
            # The rest of the record (its `connectedAt`, which the tool cache checks) stays.
            server.auth = {
                **(server.auth or {}),
                "isAuthenticated": True,
                "oauthTokens": tokens.model_dump(by_alias=True, mode="json"),
            }
            manager = self._managers.get(server.instance_id)
            if manager is not None and manager.is_open:
                _, headers = build_auth_env_and_headers(
                    manager.config, credentials_to_discovery_dict(MCPAuthMode.OAUTH.value, server.auth),
                )
                manager.update_headers(headers)
                return manager
            if manager is not None:
                del self._managers[server.instance_id]
                await manager.aclose()
            manager = await self._build_and_open(server)
            self._managers[server.instance_id] = manager
            return manager
