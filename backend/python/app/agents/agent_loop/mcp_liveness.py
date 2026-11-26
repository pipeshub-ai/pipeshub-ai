"""Revocation reaches a running attempt (MCPFIX F4, QA23-F3): ``pin ⊓ live`` for MCP servers' and toolsets' tools.

A run keeps the MCP servers it was admitted with, but every call of one of their tools first asks whether the
server is still installed and its credential still connected for the run's owner. The authoritative answer is
the run's credential handle (``ToolCredentials.mcp_auth``: the instance in the run's org, the effective record,
its binding when ``CONTROL_PLANE_CREDENTIALS`` is on). It is held per process as CPD-6 holds the live layer: an
answer is reused for ``LIVE_TTL_S``, and a change notification of the configuration store for the instance's
instance or credential keys marks it, so the next call asks again. The common call does no I/O; a revocation is
seen at the next call once its notification arrives, and after ``LIVE_TTL_S`` at worst if it never does.

A connector toolset's calls go through the same rule (``toolset_liveness``): the authoritative read is
``ToolCredentials.toolset_config`` for the run's reference, voided by a change of the toolset's credential keys or
of the toolset instance list, so a client built earlier in the attempt is not used once its credential is gone.
"""

from __future__ import annotations

import time
import weakref
from typing import TYPE_CHECKING, Any, Final

from app.agents.constants.mcp_server_constants import MCP_ROOT
from app.agents.constants.toolset_constants import get_toolset_instances_path
from app.control_plane.runtime.live_layer import LIVE_TTL_S
from harness_core.credentials.errors import CredentialRefused
from harness_core.refusals import HarnessRefusal

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.agents.agent_loop.context import AgentContext
    from app.agents.agent_loop.mcp_access import ResolvedMCPServer
    from harness_core.contracts.generated.enums import ToolErrorCode

__all__ = ["CredentialLiveness", "MCPServerRevoked", "mcp_liveness", "toolset_liveness"]

_WATCHED: Final = (f"{MCP_ROOT}/instances/", f"{MCP_ROOT}/credentials/")
# ``/services/toolsets/{instanceId}/{owner}``; the instance list is one key, so a change to it voids every answer.
_TOOLSET_WATCHED: Final = ("/services/toolsets/",)
_CLEAR_ALL: Final = "__CLEAR_ALL__"
# Confirmations are cheap to lose (the next call asks again); a process never needs more than this many.
_BOUND: Final = 4096


class MCPServerRevoked(HarnessRefusal):
    """The server was uninstalled or its credential removed after the run was admitted: the call is not made."""

    def __init__(self, server: ResolvedMCPServer, reason_code: str, *, uninstalled: bool) -> None:
        self.reason_code = reason_code
        self.tool_error_code: ToolErrorCode = "unavailable" if uninstalled else "auth"
        what = "was removed by an administrator" if uninstalled else "is no longer connected"
        super().__init__(f"The MCP server '{server.display_name}' {what} ({reason_code}); its tools cannot be called.")


def _instance_of(key: str, watched: tuple[str, ...]) -> str | None:
    for prefix in watched:
        if key.startswith(prefix):
            return key[len(prefix):].split("/", 1)[0] or None
    return None


class CredentialLiveness:
    """Per process: when each ``(org, instance, owner)`` was last confirmed live, and which instances changed since."""

    def __init__(
        self,
        *,
        ttl_s: float = LIVE_TTL_S,
        clock: Callable[[], float] = time.monotonic,
        watched: tuple[str, ...] = _WATCHED,
        voids_all: tuple[str, ...] = (),
    ) -> None:
        self._ttl_s = ttl_s
        self._clock = clock
        self._prefixes = watched
        self._voids_all = frozenset({_CLEAR_ALL, *voids_all})
        self._confirmed: dict[tuple[str, str, str], tuple[float, tuple[int, int]]] = {}
        # Raised by change notifications (the store's watch thread): a confirmation taken under an older mark is void.
        self._marks: dict[str, int] = {}
        self._all = 0
        # Weak: a test (or a reload) that makes a new service under a dead one's id is still subscribed.
        self._watched: weakref.WeakSet[Any] = weakref.WeakSet()

    def on_change(self, key: str) -> None:
        if key in self._voids_all:
            self._all += 1
            return
        instance_id = _instance_of(key, self._prefixes)
        if instance_id is not None:
            if len(self._marks) >= _BOUND:
                self._marks.clear()
                self._all += 1
            self._marks[instance_id] = self._marks.get(instance_id, 0) + 1

    def watch(self, config_service: Any) -> None:  # noqa: ANN401 - ConfigurationService
        """Subscribes to the configuration service's change notifications, once per service."""
        add = getattr(config_service, "add_change_listener", None)
        if add is None or config_service in self._watched:
            return
        self._watched.add(config_service)
        add(self.on_change)

    def version(self, instance_id: str) -> tuple[int, int]:
        return self._all, self._marks.get(instance_id, 0)

    def fresh(self, key: tuple[str, str, str]) -> bool:
        held = self._confirmed.get(key)
        return held is not None and held[1] == self.version(key[1]) and self._clock() - held[0] < self._ttl_s

    def confirm(self, key: tuple[str, str, str], version: tuple[int, int]) -> None:
        """Records a confirmation read under ``version``; one a change overtook is not recorded."""
        if version != self.version(key[1]):
            return
        if len(self._confirmed) >= _BOUND:
            self._confirmed.clear()
        self._confirmed[key] = (self._clock(), version)

    def forget(self, key: tuple[str, str, str]) -> None:
        _ = self._confirmed.pop(key, None)

    async def ensure(
        self, context: AgentContext, key: tuple[str, str, str], read: Callable[[], Awaitable[object]]
    ) -> None:
        """Runs ``read`` (the authoritative re-read) unless ``key`` is confirmed and fresh; its refusal forgets
        ``key`` and propagates."""
        if context.config_service is not None:
            self.watch(context.config_service)
        if self.fresh(key):
            return
        version = self.version(key[1])
        try:
            _ = await read()
        except CredentialRefused:
            self.forget(key)
            raise
        self.confirm(key, version)

    async def check(self, context: AgentContext, server: ResolvedMCPServer) -> None:
        """Raises ``MCPServerRevoked`` when the server's instance or the run's credential for it is gone."""
        from app.agents.mcp.instance_scope import get_instance_for_org

        reference = server.credential_ref
        if reference is None:  # a raw record, not a reference (tests, callers outside a run): nothing to re-read
            return
        try:
            await self.ensure(
                context,
                (context.org_id, server.instance_id, server.owner_id),
                lambda: context.tool_credentials().mcp_auth(reference),
            )
        except CredentialRefused as refused:
            try:
                instance = await get_instance_for_org(server.instance_id, context.config_service, context.org_id)
            except Exception:  # only names the refusal; the call is refused either way
                instance = server.instance
            raise MCPServerRevoked(server, refused.reason_code, uninstalled=not instance) from None


mcp_liveness = CredentialLiveness()
toolset_liveness = CredentialLiveness(watched=_TOOLSET_WATCHED, voids_all=(get_toolset_instances_path(""),))
