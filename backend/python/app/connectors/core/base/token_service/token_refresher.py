"""One ``TokenRefresher`` for toolset and MCP credentials held in control-plane bindings (CPD-8).

With ``CONTROL_PLANE_CREDENTIALS`` on it replaces ``ToolsetTokenRefreshService``,
``MCPTokenRefreshService`` and the write half of ``app/agents/mcp/token_refresh.py``. It is driven
by the bindings themselves: each cycle asks Node for the bindings expiring within the window
(``cpBindings {status, expiresAt}``), reads each one's record from the vault, renews it through
the strategy of the binding's kind and hands the new record to Node's binder, compare-and-set on
the binding's revision, so two replicas never both write and a revoke always wins. A refresh the
provider refuses for good takes the binding out of the scan (``error``, the user reconnects);
anything else is tried again at the next cycle. Node gives each due binding to one replica (a claim
for ``holder``, QA23-F2), so a rotating refresh token is never renewed by two. Providers are reached by the existing OAuth code
(``OAuthProvider`` for toolsets, ``oauth_client`` for MCP); nothing is written to the old paths.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast, override

from app.control_plane.runtime.bindings import BINDING_KIND
from app.core.org_context import for_org, spawn_detached, system
from app.harness.credentials.binding_writer import BindingConflict, Material
from harness_core.credentials.types import CredentialScope
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from app.harness.credentials.binding_writer import BindingWriter, DueBinding
    from app.harness.credentials.bindings import BindingDocuments
    from harness_core.clock import Clock

# Renew this long before expiry: a lease re-issued 60 s before its own expiry then finds a fresh token.
REFRESH_WINDOW_S = 600
CYCLE_LIMIT = 50
CYCLE_INTERVAL_S = 300
# An on-demand refresh of a binding another replica is renewing waits this long for that replica's write.
OWNER_WAIT_POLLS = 10
OWNER_WAIT_S = 0.5


class RefreshRefused(Exception):
    """The provider (or the record) refused the refresh; ``permanent``: only a reconnect helps."""

    def __init__(self, code: str, *, permanent: bool) -> None:
        super().__init__(code)
        self.code = code
        self.permanent = permanent


@dataclass(frozen=True, slots=True)
class Renewed:
    """The record with its new tokens, and when they expire (epoch ms)."""

    record: dict[str, Any]
    expires_at_ms: int | None

    @override
    def __repr__(self) -> str:
        return f"Renewed(expires_at_ms={self.expires_at_ms})"


class RefreshStrategy(Protocol):
    """How one kind of installation renews its credential record (03-interfaces §3.1: one per domain)."""

    kind: str

    async def renew(self, org_id: str, installation: str, owner: str, record: dict[str, Any]) -> Renewed: ...


Outcome = Literal["refreshed", "failed", "refused", "raced", "skipped"]


@dataclass
class Cycle:
    refreshed: int = 0
    failed: int = 0
    refused: int = 0
    raced: int = 0
    skipped: int = 0

    def count(self, outcome: Outcome) -> None:
        setattr(self, outcome, getattr(self, outcome) + 1)


def _owner_id(owner: str) -> str:
    return owner.split(":", 1)[1] if ":" in owner else owner


class TokenRefresher:
    def __init__(
        self,
        writer: BindingWriter,
        documents: BindingDocuments,
        strategies: Sequence[RefreshStrategy],
        clock: Clock,
        *,
        window_s: int = REFRESH_WINDOW_S,
        limit: int = CYCLE_LIMIT,
        log: HarnessLog | None = None,
        holder: str | None = None,
        pause: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        kinds = [s.kind for s in strategies]
        if len(set(kinds)) != len(kinds):
            raise ValueError("one refresh strategy per installation kind")
        self._writer = writer
        self._documents = documents
        self._strategies = {s.kind: s for s in strategies}
        self._clock = clock
        self._window = timedelta(seconds=window_s)
        self._limit = limit
        self._log = log or HarnessLog.named("app.connectors.token_refresher")
        self._locks: dict[str, asyncio.Lock] = {}
        # Renewed tokens the binder could not store (QA23-F5): a rotating provider honours the old refresh token
        # once, so they are written at the next try instead of renewing again.
        self._unstored: dict[str, Renewed] = {}
        self._task: asyncio.Task[None] | None = None
        # This replica, as Node's refresh claims name it.
        self._holder = holder or uuid.uuid4().hex
        self._pause = pause

    async def run_once(self) -> Cycle:
        """One scan: every binding due within the window, soonest first, at most ``limit``."""
        cycle = Cycle()
        before = int((self._clock.now() + self._window).timestamp() * 1000)
        # Across orgs: the scan enumerates every org's due bindings; each refresh acts for its row's org only.
        with system("token-refresh"):
            found = await self._writer.due(before, self._limit, self._holder)
        for due in found:
            try:
                with for_org(due.org_id, job="token-refresh"):
                    outcome = await self.refresh(due)
            except Exception as exc:  # one binding's store failure must not starve the others this cycle
                self._log.warning(
                    "token_refresher.failed", reasonCode="store_unavailable", errorType=type(exc).__name__,
                    orgId=due.org_id, binding=due.id,
                )
                outcome = "failed"
            cycle.count(outcome)
        self._log.info("token_refresher.cycle", **cycle.__dict__)
        return cycle

    async def refresh(self, due: DueBinding) -> Outcome:
        async with self._locks.setdefault(due.id, asyncio.Lock()):
            return await self._refresh(due)

    async def _refresh(self, due: DueBinding) -> Outcome:
        where = {"orgId": due.org_id, "binding": due.id}
        document = await self._documents.document(due.org_id, due.id)
        if not document or document.get("revoked"):
            return "skipped"
        meta = cast("dict[str, Any]", document.get("binding") or {})
        strategy = self._strategies.get(str(meta.get("kind")))
        if strategy is None:
            return await self._failed(due, RefreshRefused("refresh_unsupported", permanent=True), where)
        path = await self._documents.path_of(due.org_id, due.id)
        current = None if path is None else await self._documents.vault.read_at(
            path, due.org_id, CredentialScope(BINDING_KIND, due.id)
        )
        if current is None or not isinstance(current.material, dict):
            return "skipped"
        renewed = self._unstored.pop(due.id, None)
        if renewed is None:
            try:
                renewed = await strategy.renew(
                    due.org_id, due.installation, due.owner, cast("dict[str, Any]", current.material)
                )
            except RefreshRefused as refused:
                return await self._failed(due, refused, where)
            except Exception as exc:  # a provider or network failure: tried again next cycle
                self._log.warning("token_refresher.failed", reasonCode="refresh_failed", errorType=type(exc).__name__, **where)
                return await self._failed(due, RefreshRefused("refresh_failed", permanent=False), where, logged=True)
        try:
            _ = await self._writer.refreshed(
                due.org_id, due.id, due.revision, Material("record", renewed.record, renewed.expires_at_ms)
            )
        except BindingConflict:
            self._log.info("token_refresher.raced", **where)
            return "raced"
        except Exception as exc:
            self._unstored[due.id] = renewed
            self._log.warning("token_refresher.failed", reasonCode="store_unavailable", errorType=type(exc).__name__, **where)
            return "failed"
        self._log.info("token_refresher.refreshed", kind=strategy.kind, **where)
        return "refreshed"

    async def _failed(
        self, due: DueBinding, refused: RefreshRefused, where: dict[str, str], *, logged: bool = False
    ) -> Outcome:
        if not logged:
            self._log.warning("token_refresher.refused", reasonCode=refused.code, permanent=refused.permanent, **where)
        try:
            _ = await self._writer.refresh_failed(
                due.org_id, due.id, due.revision, refused.code, permanent=refused.permanent
            )
        except BindingConflict:
            return "raced"
        return "refused" if refused.permanent else "failed"

    async def refresh_owner(self, org_id: str, installation: str, owner: str) -> dict[str, Any] | None:
        """On demand (a call met an expired token): the owner's binding renewed now; its new record, or ``None``."""
        due = await self._writer.find_owner(org_id, installation, owner, self._holder)
        for _ in range(OWNER_WAIT_POLLS):
            if due is None or due.claimed:
                break
            # Another replica is renewing it: its write is awaited, its refresh token is not spent again.
            seen = due.revision
            await self._pause(OWNER_WAIT_S)
            due = await self._writer.find_owner(org_id, installation, owner, self._holder)
            if due is not None and due.revision != seen:
                return await self._record(due)
        if due is None or not due.claimed or await self.refresh(due) != "refreshed":
            return None
        return await self._record(due)

    async def _record(self, due: DueBinding) -> dict[str, Any] | None:
        if due.path is None:
            return None
        current = await self._documents.vault.read_at(due.path, due.org_id, CredentialScope(BINDING_KIND, due.id))
        return cast("dict[str, Any]", current.material) if current is not None and isinstance(current.material, dict) else None

    def start(self, wait: Callable[[float], Awaitable[None]] = asyncio.sleep, interval_s: float = CYCLE_INTERVAL_S) -> None:
        async def loop() -> None:
            while True:
                try:
                    _ = await self.run_once()
                except Exception as exc:  # the next cycle tries again; a dead loop would stop every refresh
                    self._log.warning("token_refresher.cycle_failed", errorType=type(exc).__name__)
                await wait(interval_s)

        if self._task is None:
            self._task = spawn_detached(loop())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            _ = task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


class ToolsetOAuthStrategy:
    """Toolset OAuth records: the toolset's OAuth app (central config or the registry) and ``OAuthProvider``."""

    kind = "toolset"

    def __init__(self, config: Any, now_ms: Callable[[], int]) -> None:  # noqa: ANN401
        from app.connectors.core.base.token_service.toolset_token_refresh_service import (
            ToolsetTokenRefreshService,
        )

        self._config = config
        self._apps = ToolsetTokenRefreshService(config)
        self._now_ms = now_ms

    async def renew(self, org_id: str, installation: str, owner: str, record: dict[str, Any]) -> Renewed:
        from app.connectors.core.base.token_service.oauth_service import (
            OAuthProvider,
            OAuthToken,
            RefreshTokenInvalidError,
        )
        from app.utils import oauth_config

        credentials = record.get("credentials")
        refresh_token = cast("dict[str, Any]", credentials).get("refresh_token") if isinstance(credentials, dict) else None
        if not refresh_token:
            raise RefreshRefused("no_refresh_token", permanent=True)
        toolset_type = str(record.get("toolsetType") or "")
        label = f"binding of {installation} for {owner}"
        flow = await self._apps.oauth_flow_config(record, toolset_type.lower(), label)
        if not flow.get("tokenUrl") or not flow.get("clientId"):
            raise RefreshRefused("oauth_app_missing", permanent=True)
        # Its config parameter is an untyped dict.
        build = cast("Callable[[dict[str, Any]], Any]", oauth_config.get_oauth_config)  # pyright: ignore[reportUnknownMemberType]
        provider = OAuthProvider(config=build(flow), configuration_service=self._config, credentials_path=label)
        try:
            token: OAuthToken = await provider.refresh_access_token(str(refresh_token), persist=False)
        except RefreshTokenInvalidError:
            raise RefreshRefused("invalid_grant", permanent=True) from None
        finally:
            await provider.close()
        expires = token.expires_at_epoch
        return Renewed(
            {**record, "credentials": token.to_dict(), "updatedAt": self._now_ms()},
            None if expires is None else expires * 1000,
        )


class McpOAuthStrategy:
    """MCP OAuth records: the token URL on the record, the client as ``resolve_client_credentials`` orders them."""

    kind = "mcp"

    def __init__(self, config: Any, now_ms: Callable[[], int]) -> None:  # noqa: ANN401
        self._config = config
        self._now_ms = now_ms

    async def renew(self, org_id: str, installation: str, owner: str, record: dict[str, Any]) -> Renewed:
        from app.agents.constants.mcp_server_constants import get_mcp_instance_path
        from app.agents.mcp import oauth_client
        from app.agents.mcp.token_refresh import resolve_client_credentials
        from app.agents.mcp.url_guard import MCPUrlPolicy

        tokens = record.get("oauthTokens")
        tokens_map = cast("dict[str, Any]", tokens) if isinstance(tokens, dict) else {}
        refresh_token = tokens_map.get("refreshToken") or tokens_map.get("refresh_token")
        token_url = tokens_map.get("tokenUrl") or tokens_map.get("token_url")
        if not refresh_token or not token_url:
            raise RefreshRefused("no_refresh_token", permanent=True)
        client_id, client_secret = await resolve_client_credentials(
            installation, _owner_id(owner), self._config, issued_by=tokens_map.get("clientId"),
        )
        if not client_id:
            raise RefreshRefused("oauth_app_missing", permanent=True)
        # As the legacy refresh does: only an org instance's own hosts may be private.
        instance = await self._config.get_config(get_mcp_instance_path(installation), default=None, use_cache=False)
        try:
            renewed = await oauth_client.refresh_access_token(
                token_url=str(token_url), client_id=client_id, client_secret=client_secret, refresh_token=str(refresh_token),
                allow_private=isinstance(instance, dict) and MCPUrlPolicy.for_instance(instance).private_ok(str(token_url)),
                resource=tokens_map.get("resource"),
                auth_method=tokens_map.get("tokenEndpointAuthMethod") or tokens_map.get("token_endpoint_auth_method"),
            )
        except oauth_client.MCPRefreshTokenInvalidError:
            raise RefreshRefused("invalid_grant", permanent=True) from None
        renewed = renewed.model_copy(update={"client_id": client_id})
        expires = (
            None
            if not renewed.expires_in
            else int((renewed.created_at.timestamp() + renewed.expires_in) * 1000)
        )
        return Renewed(
            {**record, "oauthTokens": renewed.model_dump(by_alias=True, mode="json"), "updatedAt": self._now_ms()},
            expires,
        )


async def deployment_token_refresher(config: Any, clock: Clock) -> TokenRefresher:  # noqa: ANN401
    """The connectors service's refresher with the switch on: Node's binder, the vault, both strategies."""
    from app.harness.credentials.binding_writer import node_binding_writer
    from app.harness.credentials.bindings import BindingDocuments
    from app.harness.credentials.vault import VaultSecretStore

    def now_ms() -> int:
        return int(clock.now().timestamp() * 1000)

    return TokenRefresher(
        await node_binding_writer(config),
        BindingDocuments(VaultSecretStore(config, clock)),
        [ToolsetOAuthStrategy(config, now_ms), McpOAuthStrategy(config, now_ms)],
        clock,
    )


_PROCESS: dict[int, tuple[object, TokenRefresher]] = {}


async def binding_refresher_for(config: Any) -> TokenRefresher:  # noqa: ANN401
    """The process's refresher for on-demand refreshes (a call that met an expired token), one per configuration."""
    held = _PROCESS.get(id(config))
    if held is not None and held[0] is config:
        return held[1]
    from harness_core.clock import SystemClock

    refresher = await deployment_token_refresher(config, SystemClock())
    _PROCESS[id(config)] = (config, refresher)
    return refresher


__all__ = [
    "Cycle",
    "binding_refresher_for",
    "McpOAuthStrategy",
    "RefreshRefused",
    "RefreshStrategy",
    "Renewed",
    "TokenRefresher",
    "ToolsetOAuthStrategy",
    "deployment_token_refresher",
]
