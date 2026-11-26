"""Sandbox broker ports (ARCHITECTURE §4.2).

The broker is its own process role: callers reach sandboxes only through
these ports, never through a container runtime socket. A server inside a
sandbox listens on loopback and is reached only through ``connect``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence

    from harness_core.sandbox.types import (
        BrokerHealth,
        LeaseRef,
        OrgSandboxErasure,
        RuntimeMode,
        SandboxSpec,
    )


class ByteStream(Protocol):
    """A tunnelled TCP connection into a sandbox."""

    async def read(self, max_bytes: int = 65536) -> bytes:
        """Up to ``max_bytes``; ``b""`` once the peer has closed its side."""
        ...

    async def write(self, data: bytes) -> None: ...

    async def write_eof(self) -> None: ...

    async def aclose(self) -> None: ...


class ProcessHandle(Protocol):
    """A process started by ``SandboxHandle.exec``.

    A caller that reads one stream must also drain the other: undrained
    output is buffered up to a bound, after which the process blocks.
    """

    @property
    def stdout(self) -> AsyncIterator[bytes]: ...

    @property
    def stderr(self) -> AsyncIterator[bytes]: ...

    async def write(self, data: bytes) -> None: ...

    async def close_stdin(self) -> None: ...

    async def signal(self, sig: int) -> None: ...

    async def wait(self, *, timeout_s: float | None = None) -> int:
        """The exit code (128 + N when killed by signal N).

        Raises ``TimeoutError`` after ``timeout_s`` and leaves the process running.
        """
        ...


class SandboxHandle(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def mode(self) -> RuntimeMode: ...

    @property
    def verified_binaries(self) -> Mapping[str, str]:
        """Files the broker hashed from this sandbox's image that nothing in the sandbox can change, by path:
        ``sha256:<hex>``. Empty when it vouches for none, and the caller then checks for itself."""
        ...

    async def exec(
        self, argv: Sequence[str], *, env: Mapping[str, str], stdin: bool = False
    ) -> ProcessHandle: ...

    async def connect(self, port: int) -> ByteStream:
        """A stream to 127.0.0.1:``port`` inside the sandbox, tunnelled through the broker."""
        ...

    async def put_tar(self, dest: str, stream: AsyncIterator[bytes]) -> None: ...

    async def seed_tar(self, dest: str, stream: AsyncIterator[bytes]) -> None:
        """Write a tar into a read-only scratch mount from outside; processes still see it read-only.

        Any other ``dest`` is ``InvalidSandboxRequest``: writable mounts take
        ``put_tar``, and a persistent (``source``) mount is shared state.
        """
        ...

    async def get_tar(self, src: str) -> AsyncIterator[bytes]:
        """A tar stream of ``src``; a missing path raises ``SandboxPathNotFound`` here, before iteration."""
        ...


class ReportsMemoryExhaustion(Protocol):
    """A handle that can say whether the sandbox's memory limit ended a process in it (under gVisor, the sandbox
    itself). Optional: a handle without it is read as "cannot tell" (``memory_exhausted``)."""

    async def memory_exhausted(self) -> bool: ...


async def memory_exhausted(handle: object) -> bool:
    """Asked after a sandbox's work ended unexpectedly: whether its memory limit ended it. ``False`` when the handle
    cannot tell or the asking fails; the caller then treats the end as it would without the question."""
    ask = getattr(handle, "memory_exhausted", None)
    if ask is None:
        return False
    try:
        return bool(await cast("ReportsMemoryExhaustion", handle).memory_exhausted())
    except Exception:
        return False


class HoldsSandboxFlow(Protocol):
    """A handle whose broker owns the sandbox's egress binding (SEC-A-02, R38EGRESS-FU-1): a flow hold goes through
    it, so the broker's own copy, the one it gives a restarted proxy back, is narrowed too. ``None``: this broker
    cannot (no egress proxy behind it, or older than the route); the caller then tells the proxy itself."""

    async def hold_flow(self, *, run_id: str, hold: str) -> bool | None: ...


async def hold_sandbox_flow(handle: object, *, run_id: str, hold: str) -> bool | None:
    """``hold`` on the sandbox through its broker; whether it narrowed anything, ``None`` when the handle cannot.
    A failure raises: the caller withholds what made the hold necessary."""
    if getattr(handle, "hold_flow", None) is None:
        return None
    return await cast("HoldsSandboxFlow", handle).hold_flow(run_id=run_id, hold=hold)


class SandboxBroker(Protocol):
    def modes(self) -> frozenset[RuntimeMode]: ...

    async def acquire(
        self, spec: SandboxSpec, *, lease: LeaseRef, idempotency_key: str
    ) -> SandboxHandle:
        """The sandbox for ``idempotency_key``, created on first use.

        Raises ``IsolationUnavailable`` for a mode the broker cannot provide,
        ``LeaseFenced`` when a higher fence already holds the key.
        """
        ...

    async def release(self, handle: SandboxHandle, *, keep_warm_s: int = 0) -> None:
        """Destroy the sandbox, or keep it for ``keep_warm_s`` for a re-acquire by the same key."""
        ...

    async def reap(self, *, labels: Mapping[str, str], older_than_s: int) -> int:
        """Destroy sandboxes whose labels include ``labels`` and whose age is at least ``older_than_s``."""
        ...

    async def health(self) -> BrokerHealth: ...


@runtime_checkable
class ClosesClients(Protocol):
    """A broker that holds a connection pool of its own (a remote broker's HTTP session), closed with the process."""

    async def aclose(self) -> None: ...


async def close_broker(broker: object) -> None:
    """Close what ``broker`` holds, when it holds anything: a session left open is reported unclosed at exit."""
    if isinstance(broker, ClosesClients):
        await broker.aclose()


class WarmSandboxPool(Protocol):
    """A broker that keeps a warm pool (P5.9a): sandboxes created ahead, each bound to one run at ``acquire``."""

    async def fill_warm(self) -> int:
        """Refill now, as an idle host's background refill does; the number created."""
        ...

    async def trim_warm(self) -> int:
        """Remove stale warm sandboxes and any above the target; the number removed."""
        ...

    def warm_ids(self) -> frozenset[str]:
        """The warm sandboxes this broker's pool would hand out next."""
        ...


class SessionFolderEraser(Protocol):
    """A broker that keeps persistent folders (``MountSpec.source``) erases a session's with the session."""

    async def erase_session(self, *, org_id: str, session_id: str) -> int:
        """Remove this install's persistent folders of ``org_id/session_id``; the number of folders removed.

        Raises ``InvalidSandboxRequest`` unless both ids are single safe path segments
        (``is_safe_segment``): nothing outside the session's own folder is ever reached.
        """
        ...

    async def erase_org(self, *, org_id: str) -> OrgSandboxErasure:
        """End every sandbox of this install labelled with ``org_id`` (a kept sign-in signs out first) and remove
        the org's whole folder tree (``<install>/<org>``): the org is erased (CLEANUP-G1). Idempotent.

        Raises ``InvalidSandboxRequest`` unless ``org_id`` is a single safe path segment.
        """
        ...


__all__ = [
    "ByteStream",
    "ClosesClients",
    "ProcessHandle",
    "ReportsMemoryExhaustion",
    "SandboxBroker",
    "SandboxHandle",
    "SessionFolderEraser",
    "WarmSandboxPool",
    "close_broker",
    "hold_sandbox_flow",
    "memory_exhausted",
]
