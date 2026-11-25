"""``ISandboxProvider`` and ``SandboxProviderFactory`` (design-plan §4.2, C2.14).

A provider is what a deployment runs its sandboxes on: Docker today; Kubernetes ``agent-sandbox``, a managed service
(E2B and the like) and microVMs later. Every one offers the same verbs, so the run code never knows which it has:

==============  ============================================================================================
Verb            Where
==============  ============================================================================================
create          ``create``: a new sandbox for a new key
attach          ``attach``: the live sandbox of a key (the broker's idempotent acquire); its streams are
                ``SandboxHandle.exec`` and ``SandboxHandle.connect``
exec            ``SandboxHandle.exec``
put/get files   ``SandboxHandle.put_tar`` / ``seed_tar`` / ``get_tar``
snapshot        ``snapshot``: a tar of the policy's paths, read from inside the sandbox
restore         ``restore``: a checked snapshot written back into a sandbox
suspend/resume  ``suspend`` keeps the sandbox for a time, ``resume`` takes it back by its key
destroy         ``destroy``
health          ``health``
capabilities    ``capabilities``: the provider's kind, its isolation modes and these verbs
==============  ============================================================================================

``BrokerSandboxProvider`` gives every ``SandboxBroker`` these verbs, and is itself a ``SandboxBroker``, so a caller
that needs only the broker's verbs takes it as one. ``SandboxProviderFactory`` is how a process gets its provider: the
deployment names a kind (``SANDBOX_PROVIDER``, Docker when unset), the process registers a builder for each kind it can
run, and any other kind is ``SandboxProviderUnavailable`` with a plain message, never a crash later at the first run.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final, Generic, Protocol, TypeVar

from harness_core.engines.snapshots import capture, read_archive
from harness_core.engines.snapshots import restore as restore_archive
from harness_core.sandbox.errors import (
    InvalidSandboxRequest,
    SandboxProviderUnavailable,
)
from harness_core.sandbox.ports import SandboxBroker, close_broker

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from harness_core.engines.snapshots import SnapshotPolicy
    from harness_core.sandbox.ports import SandboxHandle
    from harness_core.sandbox.types import (
        BrokerHealth,
        LeaseRef,
        RuntimeMode,
        SandboxSpec,
    )

PROVIDER_ENV: Final = "SANDBOX_PROVIDER"


class SandboxProviderKind(StrEnum):
    DOCKER = "docker"
    KUBERNETES = "k8s"
    MANAGED = "managed"
    MICROVM = "microvm"


# What each kind is called in a message a person reads.
_LABELS: Final[Mapping[SandboxProviderKind, str]] = {
    SandboxProviderKind.DOCKER: "Docker",
    SandboxProviderKind.KUBERNETES: "Kubernetes",
    SandboxProviderKind.MANAGED: "managed (hosted)",
    SandboxProviderKind.MICROVM: "microVM",
}

PROVIDER_VERBS: Final = frozenset(
    {
        "create",
        "attach",
        "exec",
        "put_files",
        "get_files",
        "snapshot",
        "restore",
        "suspend",
        "resume",
        "destroy",
        "health",
        "capabilities",
    }
)


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    kind: SandboxProviderKind
    modes: frozenset[RuntimeMode]
    verbs: frozenset[str] = PROVIDER_VERBS


class ISandboxProvider(SandboxBroker, Protocol):
    @property
    def kind(self) -> SandboxProviderKind: ...

    def capabilities(self) -> ProviderCapabilities: ...

    async def create(self, spec: SandboxSpec, *, lease: LeaseRef, key: str) -> SandboxHandle:
        """A sandbox for ``key``, made on first use; the same key returns the same live sandbox."""
        ...

    async def attach(self, spec: SandboxSpec, *, lease: LeaseRef, key: str) -> SandboxHandle:
        """The live (or kept) sandbox of ``key``: what a worker that took over a run reaches it by."""
        ...

    async def suspend(self, handle: SandboxHandle, *, keep_s: int) -> None:
        """Keep the sandbox for ``keep_s`` seconds for a ``resume`` by its key; destroyed after that."""
        ...

    async def resume(self, spec: SandboxSpec, *, lease: LeaseRef, key: str) -> SandboxHandle:
        """The suspended sandbox of ``key`` when it is still kept, else a new one."""
        ...

    async def destroy(self, handle: SandboxHandle) -> None:
        """End the sandbox; idempotent."""
        ...

    async def snapshot(self, handle: SandboxHandle, policy: SnapshotPolicy) -> bytes | None:
        """A tar of what ``policy`` admits, read from inside the sandbox; ``None`` when none of its paths exist."""
        ...

    async def restore(self, handle: SandboxHandle, archive: bytes, policy: SnapshotPolicy) -> None:
        """Write ``archive`` back into the sandbox after checking it holds only what ``policy`` admits."""
        ...


B = TypeVar("B", bound=SandboxBroker)


class BrokerSandboxProvider(Generic[B]):
    """The provider verbs over one ``SandboxBroker``; ``broker`` is the backend it runs on."""

    def __init__(self, broker: B, kind: SandboxProviderKind) -> None:
        self.broker = broker
        self._kind = kind

    @property
    def kind(self) -> SandboxProviderKind:
        return self._kind

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(self._kind, self.broker.modes())

    def modes(self) -> frozenset[RuntimeMode]:
        return self.broker.modes()

    async def aclose(self) -> None:
        await close_broker(self.broker)

    async def acquire(self, spec: SandboxSpec, *, lease: LeaseRef, idempotency_key: str) -> SandboxHandle:
        return await self.broker.acquire(spec, lease=lease, idempotency_key=idempotency_key)

    async def release(self, handle: SandboxHandle, *, keep_warm_s: int = 0) -> None:
        await self.broker.release(handle, keep_warm_s=keep_warm_s)

    async def reap(self, *, labels: Mapping[str, str], older_than_s: int) -> int:
        return await self.broker.reap(labels=labels, older_than_s=older_than_s)

    async def health(self) -> BrokerHealth:
        return await self.broker.health()

    async def create(self, spec: SandboxSpec, *, lease: LeaseRef, key: str) -> SandboxHandle:
        return await self.broker.acquire(spec, lease=lease, idempotency_key=key)

    async def attach(self, spec: SandboxSpec, *, lease: LeaseRef, key: str) -> SandboxHandle:
        return await self.broker.acquire(spec, lease=lease, idempotency_key=key)

    async def suspend(self, handle: SandboxHandle, *, keep_s: int) -> None:
        if keep_s <= 0:
            raise InvalidSandboxRequest("a suspended sandbox is kept for a positive number of seconds; destroy ends it")
        await self.broker.release(handle, keep_warm_s=keep_s)

    async def resume(self, spec: SandboxSpec, *, lease: LeaseRef, key: str) -> SandboxHandle:
        return await self.broker.acquire(spec, lease=lease, idempotency_key=key)

    async def destroy(self, handle: SandboxHandle) -> None:
        await self.broker.release(handle, keep_warm_s=0)

    async def snapshot(self, handle: SandboxHandle, policy: SnapshotPolicy) -> bytes | None:
        return await capture(handle, policy)

    async def restore(self, handle: SandboxHandle, archive: bytes, policy: SnapshotPolicy) -> None:
        await restore_archive(handle, read_archive(archive, policy))


C = TypeVar("C")


def provider_kind(raw: str | None) -> SandboxProviderKind:
    """The deployment's provider kind from ``SANDBOX_PROVIDER``'s value: Docker when unset; a value that names no kind
    is ``SandboxProviderUnavailable`` (a typo never falls back to another provider)."""
    value = (raw or "").strip().lower()
    if not value:
        return SandboxProviderKind.DOCKER
    try:
        return SandboxProviderKind(value)
    except ValueError:
        known = ", ".join(k.value for k in SandboxProviderKind)
        raise SandboxProviderUnavailable(
            f"{PROVIDER_ENV}={value!r} names no sandbox provider. Use one of: {known}.", reason="provider_unknown"
        ) from None


class SandboxProviderFactory(Generic[C, B]):
    """The providers one process can run, by kind; ``open`` builds the one the deployment names.

    ``C`` is what a builder needs (a configuration service, nothing); ``B`` the broker type the builders return.
    """

    def __init__(self, builders: Mapping[SandboxProviderKind, Callable[[C], Awaitable[B]]], *, where: str) -> None:
        self._builders = dict(builders)
        # Who is asking, for the message: "this worker", "this sandbox broker".
        self._where = where

    def available(self) -> frozenset[SandboxProviderKind]:
        return frozenset(self._builders)

    def require(self, kind: SandboxProviderKind) -> None:
        """``SandboxProviderUnavailable`` unless this process can run ``kind``."""
        if kind in self._builders:
            return
        offered = ", ".join(sorted(k.value for k in self._builders)) or "none"
        raise SandboxProviderUnavailable(
            f"The {_LABELS[kind]} sandbox provider is not available in this deployment: {self._where} runs "
            f"{offered}. Set {PROVIDER_ENV} to one of those, or leave it unset for Docker.",
            reason="provider_unavailable",
        )

    async def open(self, kind: SandboxProviderKind, context: C) -> BrokerSandboxProvider[B]:
        self.require(kind)
        broker = await self._builders[kind](context)
        return BrokerSandboxProvider(broker, kind)


__all__ = [
    "PROVIDER_ENV",
    "PROVIDER_VERBS",
    "BrokerSandboxProvider",
    "ISandboxProvider",
    "ProviderCapabilities",
    "SandboxProviderFactory",
    "SandboxProviderKind",
    "provider_kind",
]
