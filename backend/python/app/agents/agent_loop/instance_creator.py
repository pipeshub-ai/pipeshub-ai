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
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING, Any

import httpx

from app.agents.actions.util.live_record import bind_toolset_instance
from app.agents.actions.util.sign_in_rejected import (
    SIGN_IN_REJECTED_STATE,
    watching_sign_in,
)
from app.agents.agent_loop.mcp_liveness import toolset_liveness
from app.agents.tools.factories.base import ToolsetAuthError
from app.agents.tools.factories.registry import ClientFactoryRegistry
from app.harness.credentials.connect import connect_text
from app.harness.credentials.read_through import record_digest
from app.harness.credentials.sign_in_rejection import (
    DEAUTH_REASON,
    withdraw_rejected_toolset_credential,
)
from app.harness.credentials.sources import owner_scoped
from app.harness.credentials.tool_credentials import (
    UNAVAILABLE_KEY,
    scope_of,
    toolset_reference,
)
from app.services.rate_budget import (
    RatePriority,
    account_budget,
    budgeted_call,
    family_for_toolset,
)
from app.utils.logging_fields import log_event
from harness_core.credentials.errors import CredentialRefused
from harness_core.effects.failures import ToolFailure

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable
    from contextlib import AbstractAsyncContextManager

    from app.agents.agent_loop.context import AgentContext

__all__ = ["ToolInstanceCreator", "configured_name_matches"]

logger = logging.getLogger(__name__)


def _log_retry(app_name: str, attempt: int, exc: Exception, backoff: float) -> None:
    log_event(
        logger, "toolset.client.retry", level=logging.WARNING, toolset=app_name, attempt=attempt + 1,
        max_attempts=_MAX_CLIENT_CREATION_ATTEMPTS, error_type=type(exc).__name__, delay_ms=round(backoff * 1000),
    )

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

# The longest a tool call waits for the vendor budget before it answers ``rate_limited`` with the wait, so the
# executor or the engine decides instead of the call sitting out its time bound.
AGENT_BUDGET_MAX_WAIT_S = 20.0


def configured_name_matches(app_name: str, configured_name: str) -> bool:
    """Whether a configured toolset name (already normalized) belongs to `app_name`.

    Suffix matching bridges short registry names to the graph's longer ones
    ("drive" -> "googledrive"), but a name that is itself a different
    connector ("onedrive") must not be claimed by "drive", or one connector's
    credentials end up building another connector's client."""
    if configured_name == app_name:
        return True
    if not app_name or not configured_name.endswith(app_name):
        return False
    other = ClientFactoryRegistry.get_factory(configured_name)
    return other is None or other is ClientFactoryRegistry.get_factory(app_name)


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

        # Per request too: the digest of the record each cached client was built from, and the toolsets whose
        # vendor rejected the saved sign-in, which every later call of the request is refused without asking.
        self._record_digests: dict[tuple, str] = self._tool_state.setdefault("_client_record_digests", {})
        self._sign_in_rejected: dict[str, str] = self._tool_state.setdefault(SIGN_IN_REJECTED_STATE, {})

    @property
    def config_service(self) -> Any:
        return self._config_service

    async def create_instance_async(
        self,
        action_class: type,
        app_name: str,
        tool_full_name: str | None = None,
        *,
        instance_id: str | None = None,
    ) -> object:
        """Create an instance of an action class with an authenticated client.

        ``instance_id`` names the configured instance whose credentials to use
        (one of several instances of a type, P2.2); without it the app's first
        configured toolset is used, as before.

        Awaits the factory's ``create_client`` coroutine directly so we
        stay on the caller's event loop (avoids Redis cross-loop errors).
        """
        factory = ClientFactoryRegistry.get_factory(app_name)
        if factory:
            return await self._create_with_factory(
                factory, action_class, app_name, tool_full_name, instance_id
            )
        return self._fallback_creation(action_class)

    async def create_bot_instance(
        self, action_class: type, app_name: str, instance_id: str, owner: str
    ) -> tuple[object, Callable[[], Awaitable[None]]] | None:
        """The toolset's actions on ``owner``'s own connection of ``instance_id``: the org's bot account (OWD-18),
        which the run's credentials admitted for that instance alone; with the gate every call of it awaits. None
        for a toolset without a client factory."""
        factory = ClientFactoryRegistry.get_factory(app_name)
        if factory is None:
            return None
        reference = toolset_reference(instance_id, owner)
        cache_key = (app_name, instance_id, f"bot:{owner}")
        credentials = self._context.tool_credentials()
        client = self._client_cache.get(cache_key)
        if client is None:
            record = await credentials.toolset_config(reference)
            client = await self._create_client_with_retry(factory, app_name, record)
            self._client_cache[cache_key] = client
        binding = next((t for t in self._agent_toolsets if t.get("instanceId") == instance_id), {})
        name = binding.get("instanceName") or binding.get("displayName") or app_name.title()

        async def gate() -> None:
            try:
                _ = await credentials.toolset_config(reference)
            except CredentialRefused as refused:
                _ = self._client_cache.pop(cache_key, None)
                raise ToolFailure("auth", connect_text(f"{name} (bot account)", "toolset", refused.reason_code)) from None

        return self._instantiate(action_class, client, instance_id), gate

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _create_with_factory(
        self,
        factory: object,
        action_class: type,
        app_name: str,
        tool_full_name: str | None,
        instance_id: str | None = None,
    ) -> object:
        if instance_id is not None:
            toolset_config, toolset_id = self._toolset_configs.get(instance_id), instance_id
        else:
            toolset_config = self._get_toolset_config(tool_full_name) if tool_full_name else None
            toolset_id = self._tool_to_toolset_map.get(tool_full_name) if tool_full_name else None

        if toolset_config is None and instance_id is None:
            toolset_config, toolset_id = self._get_config_for_app(app_name)

        config = toolset_config or {}
        if config.get(UNAVAILABLE_KEY):
            # Not connected (QA23-F1): its tools are listed and every call answers the gate's refusal; no client.
            action = action_class.__new__(action_class)
            bind_toolset_instance(action, toolset_id)
            return action
        cache_key = (app_name, toolset_id or "default", self._user_id or "default")

        # Fast path — no lock needed
        client = self._client_cache.get(cache_key)
        if client is not None:
            self._log.debug("Reusing cached client for %s (toolset: %s)", app_name, toolset_id)
            return self._instantiate(action_class, client, toolset_id)

        # Acquire per-key lock to prevent parallel creation
        if cache_key not in self._cache_locks:
            self._cache_locks[cache_key] = asyncio.Lock()

        async with self._cache_locks[cache_key]:
            client = self._client_cache.get(cache_key)
            if client is not None:
                self._log.debug("Reusing cached client for %s (toolset: %s)", app_name, toolset_id)
                return self._instantiate(action_class, client, toolset_id)

            if toolset_config:
                self._log.debug("Creating client for %s with toolset config", app_name)
            else:
                log_event(logger, "toolset.config_missing", level=logging.WARNING, toolset=app_name)

            try:
                # State holds a reference; the record is resolved by handle only here.
                live = self._live_key(toolset_id, config)
                version = toolset_liveness.version(toolset_id or "")
                record = await self._context.tool_credentials().toolset_config(config)
                if live is not None:
                    # The read just made confirms the credential: the first call needs no second one.
                    toolset_liveness.watch(self._config_service)
                    toolset_liveness.confirm(live, version)
                client = await self._create_client_with_retry(factory, app_name, record)
                self._client_cache[cache_key] = client
                # What the client was built from: a rejection withdraws the record only if it is still this one.
                self._record_digests[cache_key] = record_digest(record)
                self._log.debug("Cached client for %s (toolset: %s)", app_name, toolset_id)
            except (ToolsetAuthError, CredentialRefused):
                raise
            except Exception as e:
                # Raised `from e`: the tool loader logs it once (toolset.load_failed).
                error_msg = str(e).lower()
                if any(s in error_msg for s in ("not authenticated", "oauth", "authentication")):
                    raise ValueError(
                        f"{app_name.capitalize()} toolset is not authenticated. "
                        "Please complete the OAuth flow first. "
                        f"Authenticate your {app_name.capitalize()} account under Your actions."
                    ) from e
                raise RuntimeError(
                    f"Failed to create {app_name} client: {e}"
                ) from e

        return self._instantiate(action_class, client, toolset_id)

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
                _log_retry(app_name, attempt, e, backoff)
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
                    _log_retry(app_name, attempt, e, backoff)
                    await asyncio.sleep(backoff)
                else:
                    raise

        raise last_exc  # type: ignore[misc]

    def _live_key(self, toolset_id: str | None, reference: dict[str, Any]) -> tuple[str, str, str] | None:
        """What ``toolset_liveness`` confirms for a reference: ``(org, instance, credential owner)``."""
        scope = scope_of(reference)
        if toolset_id is None or scope is None:
            return None
        return (self._context.org_id, toolset_id, owner_scoped(scope.name)[1])

    def call_gate(self, app_name: str, toolset_id: str | None) -> Callable[[], Awaitable[None]] | None:
        """Awaited before every call of the toolset's tools (QA23-F1, F3): an unconnected toolset is refused
        ``credential_missing``, and a credential disconnected or revoked since its client was made is refused at
        the next call (MCP-FIX2's rule, ``toolset_liveness``), its cached client dropped. None for a toolset
        without a reference (platform tools, raw records)."""
        reference = self._toolset_configs.get(toolset_id) if toolset_id else None
        if not isinstance(reference, dict):
            return None
        live = self._live_key(toolset_id, reference)
        if live is None and not reference.get(UNAVAILABLE_KEY):
            return None
        credentials = self._context.tool_credentials()
        cache_key = (app_name, toolset_id, self._user_id or "default")
        binding = next((t for t in self._agent_toolsets if t.get("instanceId") == toolset_id), {})
        name = binding.get("instanceName") or binding.get("displayName") or app_name.title()

        refused_with: list[str] = []

        async def gate() -> None:
            rejected = self._sign_in_rejected.get(toolset_id or "")
            if rejected is not None:
                raise ToolFailure("auth", rejected)
            if refused_with:
                # The tools hold the client made with the old credential: a reconnect is used by the next run.
                raise ToolFailure("auth", refused_with[0])
            try:
                if live is None:
                    _ = await credentials.toolset_config(reference)
                else:
                    await toolset_liveness.ensure(self._context, live, lambda: credentials.toolset_config(reference))
            except CredentialRefused as refused:
                _ = self._client_cache.pop(cache_key, None)
                refused_with.append(connect_text(name, "toolset", refused.reason_code))
                raise ToolFailure("auth", refused_with[0]) from None

        return gate

    def call_scope(
        self, app_name: str, toolset_id: str | None = None
    ) -> Callable[[], AbstractAsyncContextManager[object]] | None:
        """Entered around every call of the toolset's tools: the org's budget for the vendor, which its connector
        sync spends too (G-31), and, for a toolset with a credential reference, the watch for its vendor
        rejecting the saved sign-in (WP62-TS). None when neither applies."""
        budget = account_budget(self._context.org_id, family_for_toolset(app_name))
        reference = self._toolset_configs.get(toolset_id) if toolset_id else None
        live = self._live_key(toolset_id, reference) if isinstance(reference, dict) else None
        if budget is None and live is None:
            return None
        try:
            priority = RatePriority(self._context.rate_priority)
        except ValueError:
            priority = RatePriority.INTERACTIVE
        binding = next((t for t in self._agent_toolsets if t.get("instanceId") == toolset_id), {})
        name = binding.get("instanceName") or binding.get("displayName") or app_name.title()
        cache_key = (app_name, toolset_id or "default", self._user_id or "default")

        @asynccontextmanager
        async def scope() -> AsyncIterator[object]:
            async with AsyncExitStack() as stack:
                if budget is not None:
                    await stack.enter_async_context(budgeted_call(budget, priority, max_wait_s=AGENT_BUDGET_MAX_WAIT_S))
                if live is None:
                    yield None
                    return
                with watching_sign_in() as rejected:
                    yield None
                if rejected:
                    await self._sign_in_rejected_by_vendor(cache_key, live, name)

        return scope

    async def _sign_in_rejected_by_vendor(self, cache_key: tuple, live: tuple[str, str, str], name: str) -> None:
        """The vendor refused the credential the cached client holds: the client is dropped, and the record is
        withdrawn when it is still the one the client was built from. Then every later call of this request is
        refused with where to reconnect, without asking the vendor again; otherwise (the record changed since,
        or its token is only waiting for the refresher) the next call builds a client from the current record."""
        org_id, instance_id, owner_id = live
        _ = self._client_cache.pop(cache_key, None)
        used = self._record_digests.pop(cache_key, None)
        try:
            withdrawn = await withdraw_rejected_toolset_credential(
                self._config_service, org_id=org_id, instance_id=instance_id, owner_id=owner_id, used_digest=used
            )
        except Exception:  # the store is down: this request still stops calling the vendor with it
            self._log.warning("Could not withdraw the rejected credential of toolset %s", instance_id, exc_info=True)
            withdrawn = used is not None
        self._log.warning("toolset.sign_in_rejected instance=%s withdrawn=%s", instance_id, withdrawn)
        if withdrawn:
            toolset_liveness.forget(live)
            self._sign_in_rejected[instance_id] = connect_text(name, "toolset", DEAUTH_REASON)

    def _instantiate(self, action_class: type, client: object, toolset_id: str | None) -> object:
        """Instantiate the action class, passing tool_state if accepted; the action knows its instance (P2.2)."""
        params = inspect.signature(action_class.__init__).parameters
        if "state" in params:
            action = action_class(client, state=self._tool_state)
        else:
            action = action_class(client)
        bind_toolset_instance(action, toolset_id)
        return action

    def _get_config_for_app(self, app_name: str) -> tuple[dict | None, str | None]:
        """Resolve toolset config by app name via ``agent_toolsets``.

        Used as a fallback when ``tool_full_name`` is not available (e.g.
        at toolset loading time when creating the class instance before
        individual tool names are known).

        Handles the naming gap between the toolset registry (e.g. ``"drive"``)
        and the graph DB toolset names (e.g. ``"Google Drive"`` → ``"googledrive"``).
        """
        binding = self.binding_for_app(app_name)
        if binding is None:
            return None, None
        instance_id = binding["instanceId"]
        self._log.debug(
            "Resolved config for app %s via agent_toolsets (instanceId=%s)", app_name, instance_id,
        )
        return self._toolset_configs[instance_id], instance_id

    def instance_for_app(self, app_name: str) -> tuple[str, str] | None:
        """The toolset instance an app's tools run on (the one its credentials come from): its id
        and name, for the approval gate."""
        ts = self.binding_for_app(app_name)
        if ts is None:
            return None
        return ts["instanceId"], ts.get("instanceName") or ts.get("displayName") or ts.get("name") or app_name

    def instance_is_personal(self, instance_id: str) -> bool:
        """Whether the instance is a personal one, as the server marked the person's sign-in to it
        (`toolsets.py` writes the mark; nothing the person sends can set it)."""
        config = self._toolset_configs.get(instance_id)
        return isinstance(config, dict) and config.get("scope") == "personal"

    def binding_for_app(self, app_name: str) -> dict[str, Any] | None:
        """The configured toolset whose credentials an app's instance is built with when no
        instance is named: an exact name before a suffix match, then list order."""
        normalized = app_name.lower().replace(" ", "").replace("_", "")
        named = [
            ((ts.get("name") or "").lower().replace(" ", "").replace("_", ""), ts)
            for ts in self._agent_toolsets
        ]
        named.sort(key=lambda pair: pair[0] != normalized)
        for name, ts in named:
            if configured_name_matches(normalized, name):
                instance_id = ts.get("instanceId")
                if instance_id and self._toolset_configs.get(instance_id) is not None:
                    return ts
        return None

    def bindings_for_app(self, app_name: str) -> list[dict[str, Any]]:
        """Every configured toolset of an app's type that has credentials, one per instance, in list order."""
        normalized = app_name.lower().replace(" ", "").replace("_", "")
        found: list[dict[str, Any]] = []
        seen: set[str] = set()
        for ts in self._agent_toolsets:
            name = (ts.get("name") or "").lower().replace(" ", "").replace("_", "")
            instance_id = ts.get("instanceId")
            if not configured_name_matches(normalized, name) or not instance_id or instance_id in seen:
                continue
            if self._toolset_configs.get(instance_id) is not None:
                seen.add(instance_id)
                found.append(ts)
        return found

    def _get_toolset_config(self, tool_full_name: str) -> dict | None:
        """Resolve toolset config for a tool from the context's maps."""
        toolset_id = self._tool_to_toolset_map.get(tool_full_name)
        if not toolset_id:
            self._log.debug("No toolset ID for tool %s", tool_full_name)
            return None

        config = self._toolset_configs.get(toolset_id)
        if not config:
            log_event(logger, "toolset.config_missing", level=logging.WARNING, toolset_id=toolset_id)
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
