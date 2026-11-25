"""Egress proxy ports (P5.4 §1.3, P5.4b): the binding table, the token source, name resolution, durable state."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from harness_core.sandbox.egress.bindings import Revocation
    from harness_core.sandbox.egress.types import EgressBinding, IssuedToken
    from harness_core.sandbox.types import TokenRoute


class EgressBindings(Protocol):
    """The proxy's sandbox table, as the broker writes it.

    ``register`` is idempotent per sandbox, refuses a placeholder another live
    sandbox holds (``InvalidSandboxSpec``, reason ``placeholder_in_use``) and
    replaces a stale binding on the same proxy address; ``unregister`` is
    idempotent.
    """

    async def register(self, binding: EgressBinding) -> None: ...

    async def unregister(self, sandbox_id: str) -> None: ...

    async def bound(self) -> frozenset[str]: ...


class EgressRevocations(Protocol):
    """An incident stop on the proxy's table (C6.14): the covered live bindings end at once, their tokens and open
    connections with them, and none of those sandboxes is bound again. Returns the revoked sandbox ids."""

    async def revoke(self, revocation: Revocation) -> tuple[str, ...]: ...


class TokenSource(Protocol):
    """Short-lived tokens bound to one sandbox's run; the sandbox never sees them.

    Raises ``TokenUnavailable`` when it cannot issue one (unknown credential
    ref, run no longer leased, authority down).
    """

    async def issue(self, route: TokenRoute, binding: EgressBinding) -> IssuedToken: ...


class HostResolver(Protocol):
    """Addresses of a destination name; ``HostUnresolvable`` when there are none."""

    async def resolve(self, host: str) -> tuple[str, ...]: ...


class EgressStateStore(Protocol):
    """Where a proxy keeps what must outlive it: text values under keys, each with an expiry.

    Keys are the caller's (no namespace is added beyond the adapter's own
    scope); values are opaque text the caller seals. ``put_if_absent`` returns
    the value the store holds afterwards (the first writer's) and sets no
    expiry; ``ttl_s=None`` neither. An entry past its TTL is gone from ``entries``. Every method raises on a store failure.
    """

    async def put(self, key: str, value: str, *, ttl_s: int | None) -> None: ...

    async def put_if_absent(self, key: str, value: str) -> str: ...

    async def delete(self, key: str) -> None: ...

    async def entries(self) -> tuple[tuple[str, str], ...]: ...

    async def replace(self, key: str, expected: str, value: str) -> bool:
        """Writes ``value`` only while the key still holds ``expected``, keeping its expiry; ``False`` when it does
        not (changed, expired or gone). The key rotation's reseal (ADR-KEY-01)."""
        ...


__all__ = ["EgressBindings", "EgressRevocations", "EgressStateStore", "HostResolver", "TokenSource"]
