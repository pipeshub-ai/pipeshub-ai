"""In-memory adapters of the Python runtime ports (03-interfaces §3.2, §4.5, §5.2, §5.4), test support only (R3 M14).

Each passes the contract in ``app.control_plane.testing.contracts`` that its production adapter must pass too.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

from app.control_plane.contracts.generated.live_layer import (
    LiveLayer as LiveLayerDocument,
)
from app.control_plane.runtime.bindings import binding_version_digest
from app.control_plane.runtime.live_layer import (
    LIVE_TTL_S,
    LiveLayerUnavailable,
    ReloadingLiveLayerCache,
)
from app.control_plane.runtime.problems import problem
from app.control_plane.runtime.types import ItemLoad, LoadedTool
from harness_core.credentials.errors import (
    CredentialExpired,
    CredentialPurposeMismatch,
    CredentialRevoked,
    CredentialTtlInvalid,
    CredentialUnavailable,
    CredentialUnknown,
)
from harness_core.credentials.types import (
    MAX_TTL_S,
    CredentialScope,
    Secret,
    ShortLivedCredential,
)
from harness_core.driver.run_driver import CapabilityLost

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pydantic import JsonValue

    from app.control_plane.contracts.generated.change_event import ChangeEvent
    from app.control_plane.contracts.generated.effective_set import (
        EffectiveItem,
        PinnedBinding,
    )
    from app.control_plane.contracts.generated.problem import Problem
    from app.control_plane.runtime.ports import CredentialBroker, ItemRuntime
    from app.control_plane.runtime.types import (
        LiveDescriptor,
        RunContext,
        Unsubscribe,
    )
    from harness_core.clock import Clock
    from harness_core.credentials.types import CredentialPurpose


class InMemoryItemRuntime:
    """Loads the pinned tools of ready items as they are, opening one session per item it loads."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.open_sessions = 0

    async def load(self, items: Sequence[EffectiveItem], ctx: RunContext, credentials: CredentialBroker) -> ItemLoad:
        ready = [item for item in items if item.state == "ready" and item.kind is not None and item.kind.root == self.kind]
        tools = tuple(
            LoadedTool(tool.tool_id, tool.descriptor_digest.root, handle=(ctx.run_id, tool.tool_id))
            for item in ready
            for tool in item.tools
        )
        self.open_sessions += len(ready)
        return ItemLoad(tools=tools, closers=tuple(self._closer for _ in ready))

    def verify(self, item: EffectiveItem, live: LiveDescriptor) -> Problem | None:
        pinned = {tool.tool_id: tool.descriptor_digest.root for tool in item.tools}
        if pinned.get(live.tool_id) == live.digest:
            return None
        return problem(
            "CP_DESCRIPTOR_CHANGED",
            "This tool changed since the run started; an admin must approve the new version.",
            installation=item.installation.root,
        )

    async def _closer(self) -> None:
        self.open_sessions -= 1


class InMemoryItemRuntimeRegistry:
    def __init__(self) -> None:
        self._runtimes: dict[str, ItemRuntime] = {}

    def register(self, runtime: ItemRuntime) -> None:
        if runtime.kind in self._runtimes:
            raise ValueError(f"a runtime for kind {runtime.kind} is already registered")
        self._runtimes[runtime.kind] = runtime

    def for_kind(self, kind: str) -> ItemRuntime:
        runtime = self._runtimes.get(kind)
        if runtime is None:
            raise CapabilityLost(missing=[kind])
        return runtime


@dataclass
class _BindingVersions:
    """One binding's secret versions in the vault, oldest first, and whether it is tombstoned."""

    versions: list[tuple[str, JsonValue]] = field(default_factory=lambda: [])
    revoked: bool = False


class InMemoryCredentialBroker:
    """Leases by binding reference over a vault that keeps each binding's versions and its tombstone."""

    KIND = "binding"

    def __init__(self, clock: Clock, ids: Callable[[], str]) -> None:
        self._clock = clock
        self._ids = ids
        self._vault: dict[str, _BindingVersions] = {}
        self._handles: dict[str, ShortLivedCredential] = {}

    def store(self, binding: str, material: JsonValue) -> str:
        """The binder writes (or a refresh rotates) a version; it becomes the current one. Answers its digest."""
        held = self._vault.setdefault(binding, _BindingVersions())
        version_digest = binding_version_digest(binding, len(held.versions) + 1)
        held.versions.append((version_digest, material))
        return version_digest

    def tombstone(self, binding: str) -> None:
        self._vault.setdefault(binding, _BindingVersions()).revoked = True

    async def lease(self, binding: PinnedBinding, purpose: CredentialPurpose, ttl_s: int) -> ShortLivedCredential:
        if not 0 < ttl_s <= MAX_TTL_S:
            raise CredentialTtlInvalid(f"a lease lives 1..{MAX_TTL_S} s")
        ref = binding.ref
        held = self._current(ref)
        if binding.version_digest.root not in [digest for digest, _ in held.versions]:
            raise CredentialUnavailable("the pinned version is not a version of this binding")
        handle = ShortLivedCredential(
            handle=f"crh_{self._ids()}",
            purpose=purpose,
            scope=CredentialScope(self.KIND, ref),
            version=held.versions[-1][0],
            expires_at=self._clock.now() + timedelta(seconds=ttl_s),
        )
        self._handles[handle.handle] = handle
        return handle

    async def resolve(self, handle: ShortLivedCredential, purpose: CredentialPurpose) -> Secret:
        issued = self._handles.get(handle.handle)
        if issued is None:
            raise CredentialUnknown("no such lease")
        if issued.purpose != purpose:
            raise CredentialPurposeMismatch("the lease is for another purpose")
        if self._clock.now() >= issued.expires_at:
            raise CredentialExpired("the lease expired")
        held = self._current(issued.scope.name)
        material = dict(held.versions)[issued.version]
        return Secret(issued.version, material)

    async def release(self, handle: ShortLivedCredential) -> None:
        _ = self._handles.pop(handle.handle, None)

    def _current(self, binding: str) -> _BindingVersions:
        held = self._vault.get(binding)
        if held is None or not held.versions:
            raise CredentialUnavailable("no secret is bound")
        if held.revoked:
            raise CredentialRevoked("the binding was revoked")
        return held


class InMemoryChangeNotifier:
    """Delivers each event to every subscriber at publish, in publish order."""

    def __init__(self) -> None:
        self._handlers: list[Callable[[ChangeEvent], None]] = []

    async def publish(self, event: ChangeEvent) -> None:
        for handler in list(self._handlers):
            handler(event.model_copy(deep=True))

    def subscribe(self, handler: Callable[[ChangeEvent], None]) -> Unsubscribe:
        def own(event: ChangeEvent) -> None:
            handler(event)

        self._handlers.append(own)

        def unsubscribe() -> None:
            if own in self._handlers:
                self._handlers.remove(own)

        return unsubscribe


class InMemoryLiveLayerSource:
    """What the internal live route answers, set by a test: a document per org, or an outage."""

    def __init__(self) -> None:
        self.documents: dict[str, LiveLayerDocument] = {}
        self.unreachable = False
        self.calls: list[tuple[str, int | None]] = []

    def put(self, org_id: str, revision: int, *layers: JsonValue, revoked: Sequence[str] = ()) -> None:
        """``layers``: ``{"layer": <policy layer of kind live>}`` with ``agentKey`` for one agent's runs."""
        self.documents[org_id] = LiveLayerDocument.model_validate(
            {"schemaVersion": 1, "orgId": org_id, "revision": revision, "layers": list(layers), "revokedBindings": list(revoked)}
        )

    async def load(self, org_id: str, since: int | None) -> LiveLayerDocument | None:
        self.calls.append((org_id, since))
        if self.unreachable:
            raise LiveLayerUnavailable("the live route is unreachable")
        document = self.documents.get(org_id)
        if document is None:
            document = LiveLayerDocument.model_validate(
                {"schemaVersion": 1, "orgId": org_id, "revision": 0, "layers": [], "revokedBindings": []}
            )
        return None if since is not None and document.revision.root == since else document


def live_approvals(rules: JsonValue, agent_key: str | None = None) -> JsonValue:
    """A member of a live layer document narrowing approvals, for ``InMemoryLiveLayerSource.put``."""
    scoped: dict[str, JsonValue] = {"layer": {"schemaVersion": 1, "scope": {"kind": "live"}, "approvals": rules}}
    if agent_key is not None:
        scoped["agentKey"] = agent_key
    return scoped


class InMemoryLiveLayerCache(ReloadingLiveLayerCache):
    """The production cache over an ``InMemoryLiveLayerSource`` (``source``) a test writes to."""

    def __init__(self, clock: Clock, *, ttl_s: float = LIVE_TTL_S, bound: int = 1000) -> None:
        self.source = InMemoryLiveLayerSource()
        super().__init__(self.source, clock, ttl_s=ttl_s, bound=bound)


__all__ = [
    "InMemoryChangeNotifier",
    "InMemoryCredentialBroker",
    "InMemoryItemRuntime",
    "InMemoryItemRuntimeRegistry",
    "InMemoryLiveLayerCache",
    "InMemoryLiveLayerSource",
    "live_approvals",
]
