"""Ports of the credential service (P2.4a).

Contract suites: ``harness_core.testing.contracts.credentials``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.credentials.types import (
        CredentialPrincipal,
        CredentialPurpose,
        CredentialScope,
        Secret,
        ShortLivedCredential,
    )


class CredentialService(Protocol):
    async def issue(
        self,
        purpose: CredentialPurpose,
        principal: CredentialPrincipal,
        scope: CredentialScope,
        *,
        ttl_s: int,
    ) -> ShortLivedCredential:
        """A handle for ``scope``'s current version, usable only for ``purpose`` until it expires.
        Raises a ``CredentialRefused``."""
        ...

    async def resolve(self, handle: str, purpose: CredentialPurpose) -> Secret:
        """The secret behind ``handle``, at the effect boundary only; ``purpose`` is the caller's
        own and must equal the one the handle was issued for."""
        ...

    async def revoke(self, handle: str) -> None:
        """The next ``resolve`` of ``handle`` fails; idempotent; an unknown handle is a no-op."""
        ...

    async def rotate(self, tenant: str, scope: CredentialScope, material: JsonValue) -> str:
        """Stores a new version and returns it; handles issued before keep resolving
        their version until they expire."""
        ...


class SecretSource(Protocol):
    """Where one kind of secret lives. ``version=None`` reads the current version."""

    async def read(self, tenant: str, scope: CredentialScope, version: str | None = None) -> Secret | None: ...

    async def write(self, tenant: str, scope: CredentialScope, material: JsonValue) -> str:
        """A new current version; the previous stays readable for the retention.
        A source its owner writes raises ``CredentialReadOnly``."""
        ...


__all__ = ["CredentialService", "SecretSource"]
