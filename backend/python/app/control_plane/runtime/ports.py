"""Ports of the Python runtime side of the control plane (03-interfaces §3.2, §4.5, §5.2, §5.4).

Contract suites: ``app.control_plane.testing.contracts``; in-memory adapters: ``app.control_plane.testing.memory``.
Production adapters: ``ItemRuntime`` per kind and its registry (CPD-7), ``CredentialBroker`` over
``HandleCredentialService`` with a binding source (CPD-8), ``BrokerChangeNotifier`` (``runtime/notifier.py``) and
``LiveLayerCache`` (``runtime/live_layer.py``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from app.control_plane.contracts.generated.change_event import ChangeEvent
    from app.control_plane.contracts.generated.effective_set import (
        EffectiveItem,
        PinnedBinding,
    )
    from app.control_plane.contracts.generated.problem import Problem
    from app.control_plane.contracts.generated.resolved_run_config import (
        ResolvedRunConfig,
    )
    from app.control_plane.runtime.live_layer import LiveLayer
    from app.control_plane.runtime.pinning import PinSubject
    from app.control_plane.runtime.types import (
        ItemLoad,
        LiveDescriptor,
        RunContext,
        Unsubscribe,
    )
    from harness_core.credentials.types import (
        CredentialPurpose,
        Secret,
        ShortLivedCredential,
    )


class CredentialBroker(Protocol):
    """Leases a pinned binding's secret, bound to a purpose and a TTL; never hands out a revoked one."""

    async def lease(self, binding: PinnedBinding, purpose: CredentialPurpose, ttl_s: int) -> ShortLivedCredential:
        """A handle for the pinned version, or a newer refreshed version of the same binding; raises a
        ``CredentialRefused`` for a revoked binding or a digest of no version of it."""
        ...

    async def resolve(self, handle: ShortLivedCredential, purpose: CredentialPurpose) -> Secret:
        """The secret behind ``handle`` at the effect boundary; the vault's tombstone wins over the handle."""
        ...

    async def release(self, handle: ShortLivedCredential) -> None: ...


class ItemRuntime(Protocol):
    """Turns the pinned effective-set items of one kind into tools, mounts and prompt material."""

    kind: str

    async def load(self, items: Sequence[EffectiveItem], ctx: RunContext, credentials: CredentialBroker) -> ItemLoad:
        """Exactly the pinned tools of the ready items; an unavailable item is never loaded."""
        ...

    def verify(self, item: EffectiveItem, live: LiveDescriptor) -> Problem | None:
        """``CP_DESCRIPTOR_CHANGED`` when the live descriptor is not the pinned one (FR-28), else ``None``."""
        ...


class ItemRuntimeRegistry(Protocol):
    def register(self, runtime: ItemRuntime) -> None:
        """Refuses a second runtime for a kind."""
        ...

    def for_kind(self, kind: str) -> ItemRuntime:
        """Raises ``CapabilityLost`` for a kind no runtime serves."""
        ...


class ChangeNotifier(Protocol):
    """Committed control-plane changes, ordered per org, at least once; a late subscriber gets no replay."""

    async def publish(self, event: ChangeEvent) -> None: ...

    def subscribe(self, handler: Callable[[ChangeEvent], None]) -> Unsubscribe: ...


class LiveLayerCache(Protocol):
    """Each org's narrow-only live layer, reloaded from the live route when an event names the org or its time to
    live has passed (02-lld §8.3)."""

    async def current(self, org_id: str) -> LiveLayer:
        """Never raises: a layer that could not be reloaded for longer than its time to live is ``stale``."""
        ...

    def on_change(self, event: ChangeEvent) -> None:
        """Applies the revision rule: a duplicate is ignored; the next event or a gap marks the org to reload."""
        ...


__all__ = [
    "ChangeNotifier",
    "CredentialBroker",
    "ItemRuntime",
    "ItemRuntimeRegistry",
    "LiveLayerCache",
    "RunConfigPinner",
]


class RunConfigPinner(Protocol):
    """The admission-time entry point (03-interfaces §4.4). PipesHub pins in Node; a Loom host and the shared vectors
    pin with this one."""

    async def pin(self, subject: PinSubject) -> ResolvedRunConfig | Problem:
        """The same subject and snapshot pin the same digest; a child is at most its parent on every ceiling; a
        widening is a ``Problem`` naming the dimension and the layer."""
        ...

