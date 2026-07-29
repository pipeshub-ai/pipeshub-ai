"""Creates authenticated toolset class instances for the agent-loop path.

`ToolInstanceCreator` is initialized once per request with an `AgentContext`
and caches created API clients per ``(app_name, toolset_id, user_id)`` so
that multiple tool calls within the same request reuse the same authenticated
client instead of re-creating OAuth/MSAL/Graph from scratch each time.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import TYPE_CHECKING, Any

import httpx

from app.agents.tools.factories.base import ToolsetAuthError
from app.agents.tools.factories.registry import ClientFactoryRegistry

if TYPE_CHECKING:
    from app.agents.agent_loop.context import AgentContext

__all__ = ["ToolInstanceCreator", "close_cached_clients"]

logger = logging.getLogger(__name__)

# Preference order for duck-typed teardown: `aclose` (httpx.AsyncClient and
# most async SDK clients) before the sync `close`/`shutdown` names some
# connector wrappers use. Only the first present method is called — calling
# multiple would double-close some clients.
_CLOSE_METHOD_NAMES = ("aclose", "close", "shutdown")


async def _close_one(obj: Any, log: logging.Logger, label: str) -> None:
    for method_name in _CLOSE_METHOD_NAMES:
        method = getattr(obj, method_name, None)
        if not callable(method):
            continue
        try:
            if inspect.iscoroutinefunction(method):
                await method()
            else:
                # Sync `shutdown()` implementations (e.g. the connector
                # action wrappers' background-loop teardown) call
                # `Thread.join()`, which blocks — run off the event loop so
                # one slow-to-stop client can't stall the whole cleanup.
                await asyncio.to_thread(method)
        except Exception as exc:  # noqa: BLE001 - best-effort teardown, never fail the request on it
            log.debug("close_cached_clients: %s.%s() failed for %s: %s", type(obj).__name__, method_name, label, exc)
        return


async def close_cached_clients(tool_state: dict[str, Any], log: logging.Logger | None = None) -> None:
    """Best-effort teardown of every client/toolset instance created for
    this request.

    Two categories of resources accumulate on `tool_state` over the life of
    a request and are never closed by anything else:

    - `_client_cache` (populated by `ToolInstanceCreator`): OAuth/HTTP API
      clients (httpx, MSAL, Google SDK, ...) holding open connection pools.
    - `_toolset_instances` (populated by `tool_loader.py`): connector action
      wrapper instances, several of which (Airtable, Box, Dropbox, GitLab,
      LinkedIn, ...) own a background `asyncio` event loop + daemon thread.

    Called once, at request-end, from both `stream_bridge.py` and
    `chat_modes/bridge.py`'s `_produce()` `finally` blocks. Swallows all
    errors — a broken client's close() must never break the response.
    """
    close_log = log or logger

    client_cache = tool_state.get("_client_cache") or {}
    for cache_key, client in list(client_cache.items()):
        await _close_one(client, close_log, label=f"client_cache{cache_key!r}")
    client_cache.clear()

    toolset_instances = tool_state.get("_toolset_instances") or []
    for instance in toolset_instances:
        await _close_one(instance, close_log, label=type(instance).__name__)
    toolset_instances.clear()

_TRANSIENT_EXCEPTIONS: tuple[type[Exception], ...] = (
    httpx.RemoteProtocolError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.ConnectError,
    httpx.TimeoutException,
    ConnectionError,
    OSError,
)

_MAX_CLIENT_CREATION_ATTEMPTS = 3


class ToolInstanceCreator:
    """Creates toolset class instances with proper authenticated clients.

    Accepts an `AgentContext` and reads typed fields directly — no
    indirect ``retrieval_service.config_service`` lookups.
    """

    def __init__(self, context: "AgentContext") -> None:
        self._context = context
        self._log = context.logger or logger
        self._config_service = context.config_service
        self._tool_to_toolset_map = context.tool_to_toolset_map
        self._toolset_configs = context.toolset_configs
        self._agent_toolsets = context.agent_toolsets
        self._user_id = context.user_id
        # tool_state is the mutable ChatState-shaped dict that factories
        # and action classes still need for OAuth token lookups, org_id, etc.
        self._tool_state = context.tool_state

        # Per-request client cache lives on tool_state so it's shared
        # across all ToolInstanceCreator instances within the same request.
        if "_client_cache" not in self._tool_state:
            self._tool_state["_client_cache"] = {}
        self._client_cache: dict[tuple, object] = self._tool_state["_client_cache"]

        if "_client_cache_locks" not in self._tool_state:
            self._tool_state["_client_cache_locks"] = {}
        self._cache_locks: dict[tuple, asyncio.Lock] = self._tool_state["_client_cache_locks"]

    @property
    def config_service(self) -> Any:
        return self._config_service

    async def create_instance_async(
        self,
        action_class: type,
        app_name: str,
        tool_full_name: str | None = None,
    ) -> object:
        """Create an instance of an action class with an authenticated client.

        Awaits the factory's ``create_client`` coroutine directly so we
        stay on the caller's event loop (avoids Redis cross-loop errors).
        """
        factory = ClientFactoryRegistry.get_factory(app_name)
        if factory:
            return await self._create_with_factory(factory, action_class, app_name, tool_full_name)
        return self._fallback_creation(action_class)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _create_with_factory(
        self,
        factory: object,
        action_class: type,
        app_name: str,
        tool_full_name: str | None,
    ) -> object:
        toolset_config = self._get_toolset_config(tool_full_name) if tool_full_name else None
        toolset_id = self._tool_to_toolset_map.get(tool_full_name) if tool_full_name else None

        if toolset_config is None:
            toolset_config, toolset_id = self._get_config_for_app(app_name)

        config = toolset_config or {}
        cache_key = (app_name, toolset_id or "default", self._user_id or "default")

        # Fast path — no lock needed
        client = self._client_cache.get(cache_key)
        if client is not None:
            self._log.debug("Reusing cached client for %s (toolset: %s)", app_name, toolset_id)
            return self._instantiate(action_class, client)

        # Acquire per-key lock to prevent parallel creation
        if cache_key not in self._cache_locks:
            self._cache_locks[cache_key] = asyncio.Lock()

        async with self._cache_locks[cache_key]:
            client = self._client_cache.get(cache_key)
            if client is not None:
                self._log.debug("Reusing cached client for %s (toolset: %s)", app_name, toolset_id)
                return self._instantiate(action_class, client)

            if toolset_config:
                self._log.debug("Creating client for %s with toolset config", app_name)
            else:
                self._log.warning("No toolset config for %s, using empty config", app_name)

            try:
                client = await self._create_client_with_retry(factory, app_name, config)
                self._client_cache[cache_key] = client
                self._log.debug("Cached client for %s (toolset: %s)", app_name, toolset_id)
            except ToolsetAuthError:
                raise
            except Exception as e:
                self._log.error("Failed to create client for %s: %s", app_name, e, exc_info=True)
                error_msg = str(e).lower()
                if any(s in error_msg for s in ("not authenticated", "oauth", "authentication")):
                    raise ValueError(
                        f"{app_name.capitalize()} toolset is not authenticated. "
                        "Please complete the OAuth flow first. "
                        f"Go to Settings > Toolsets to authenticate your {app_name.capitalize()} account."
                    ) from e
                raise RuntimeError(
                    f"Failed to create {app_name} client: {e}"
                ) from e

        return self._instantiate(action_class, client)

    async def _create_client_with_retry(
        self, factory: object, app_name: str, config: dict,
    ) -> object:
        """Call ``factory.create_client`` with retry on transient network errors.

        Connector factories often make network calls during client creation
        (e.g. OAuth token exchange, accessible-resource resolution for
        Atlassian). These are susceptible to transient failures (dropped
        connections, timeouts) that succeed on retry.
        """
        last_exc: Exception | None = None
        for attempt in range(_MAX_CLIENT_CREATION_ATTEMPTS):
            try:
                return await factory.create_client(
                    self._config_service,
                    self._log,
                    config,
                    self._tool_state,
                )
            except ToolsetAuthError:
                raise
            except _TRANSIENT_EXCEPTIONS as e:
                last_exc = e
                if attempt == _MAX_CLIENT_CREATION_ATTEMPTS - 1:
                    break
                backoff = 0.5 * (2 ** attempt)
                self._log.warning(
                    "Transient error creating %s client (attempt %d/%d): %s — retrying in %.1fs",
                    app_name, attempt + 1, _MAX_CLIENT_CREATION_ATTEMPTS,
                    type(e).__name__, backoff,
                )
                await asyncio.sleep(backoff)
            except Exception as e:
                error_msg = str(e).lower()
                is_transient = any(
                    marker in error_msg
                    for marker in ("disconnected", "timed out", "timeout", "connection reset")
                )
                if is_transient:
                    last_exc = e
                    if attempt == _MAX_CLIENT_CREATION_ATTEMPTS - 1:
                        break
                    backoff = 0.5 * (2 ** attempt)
                    self._log.warning(
                        "Transient error creating %s client (attempt %d/%d): %s — retrying in %.1fs",
                        app_name, attempt + 1, _MAX_CLIENT_CREATION_ATTEMPTS,
                        e, backoff,
                    )
                    await asyncio.sleep(backoff)
                else:
                    raise

        raise last_exc  # type: ignore[misc]

    def _instantiate(self, action_class: type, client: object) -> object:
        """Instantiate the action class, passing tool_state if accepted."""
        params = inspect.signature(action_class.__init__).parameters
        if "state" in params:
            return action_class(client, state=self._tool_state)
        return action_class(client)

    def _get_config_for_app(self, app_name: str) -> tuple[dict | None, str | None]:
        """Resolve toolset config by app name via ``agent_toolsets``.

        Used as a fallback when ``tool_full_name`` is not available (e.g.
        at toolset loading time when creating the class instance before
        individual tool names are known).

        Handles the naming gap between the toolset registry (e.g. ``"drive"``)
        and the graph DB toolset names (e.g. ``"Google Drive"`` → ``"googledrive"``).
        """
        normalized = app_name.lower().replace(" ", "").replace("_", "")
        for ts in self._agent_toolsets:
            name = (ts.get("name") or "").lower().replace(" ", "").replace("_", "")
            if name == normalized or name.endswith(normalized):
                instance_id = ts.get("instanceId")
                if instance_id:
                    config = self._toolset_configs.get(instance_id)
                    if config is not None:
                        self._log.debug(
                            "Resolved config for app %s via agent_toolsets (instanceId=%s)",
                            app_name, instance_id,
                        )
                        return config, instance_id
        return None, None

    def _get_toolset_config(self, tool_full_name: str) -> dict | None:
        """Resolve toolset config for a tool from the context's maps."""
        toolset_id = self._tool_to_toolset_map.get(tool_full_name)
        if not toolset_id:
            self._log.debug("No toolset ID for tool %s", tool_full_name)
            return None

        config = self._toolset_configs.get(toolset_id)
        if not config:
            self._log.warning(
                "Toolset config not found for ID %s (tool: %s)", toolset_id, tool_full_name,
            )
        return config

    def _fallback_creation(self, action_class: type) -> object:
        """Create an instance without an API client (internal tools like
        retrieval, calculator, etc. that only need state or no args)."""
        for args in (
            {"state": self._tool_state},
            {},
            {"client": {}},
            {"client": None},
        ):
            try:
                # Map keyword-style attempts to the constructor
                if "state" in args:
                    instance = action_class(state=self._tool_state)
                elif "client" in args:
                    instance = action_class(args["client"])
                else:
                    instance = action_class()
                if hasattr(instance, "set_state"):
                    instance.set_state(self._tool_state)
                return instance
            except (TypeError, Exception):
                continue
        raise RuntimeError(f"Cannot instantiate {action_class.__name__} with any known signature")
