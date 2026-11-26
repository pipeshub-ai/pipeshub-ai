"""``CredentialLeases``: the effect boundary's view of the credential service.

One issuance per handle lifetime per (purpose, scope), re-issued shortly
before expiry (REPORT §6.5.4: one round trip per token lifetime, not per
call). A revoked handle is never replaced: revocation must hold for the rest
of the run.

Given a ``ResolvedSecrets``, every record resolved is added to it so the effect boundary can mask its values exactly
(SEC4); ``revoke_all`` clears it with the handles.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

from harness_core.credentials.errors import CredentialExpired
from harness_core.credentials.types import MAX_TTL_S, REFRESH_BEFORE_S

if TYPE_CHECKING:
    from harness_core.clock import Clock
    from harness_core.credentials.ports import CredentialService
    from harness_core.credentials.types import (
        CredentialPrincipal,
        CredentialPurpose,
        CredentialScope,
        Secret,
        ShortLivedCredential,
    )
    from harness_core.security.secrets import ResolvedSecrets


class CredentialLeases:
    def __init__(
        self,
        service: CredentialService,
        clock: Clock,
        *,
        ttl_s: int = MAX_TTL_S,
        refresh_before_s: int = REFRESH_BEFORE_S,
        resolved: ResolvedSecrets | None = None,
    ) -> None:
        self._service = service
        self._clock = clock
        self._ttl_s = ttl_s
        self._refresh_before = timedelta(seconds=refresh_before_s)
        self._resolved = resolved
        self._held: dict[tuple[CredentialPurpose, CredentialScope], ShortLivedCredential] = {}
        self._locks: dict[tuple[CredentialPurpose, CredentialScope], asyncio.Lock] = {}

    async def _issue(self, purpose: CredentialPurpose, principal: CredentialPrincipal, scope: CredentialScope) -> ShortLivedCredential:
        # A long-lived holder (the model gateway serves many runs) forgets what has lapsed.
        now = self._clock.now()
        for key in [k for k, c in self._held.items() if c.expires_at <= now and k != (purpose, scope)]:
            del self._held[key]
            _ = self._locks.pop(key, None)
        credential = await self._service.issue(purpose, principal, scope, ttl_s=self._ttl_s)
        self._held[purpose, scope] = credential
        return credential

    async def secret(self, purpose: CredentialPurpose, principal: CredentialPrincipal, scope: CredentialScope) -> Secret:
        key = (purpose, scope)
        async with self._locks.setdefault(key, asyncio.Lock()):
            held = self._held.get(key)
            if held is None or held.expires_at - self._clock.now() <= self._refresh_before:
                held = await self._issue(purpose, principal, scope)
            try:
                secret = await self._service.resolve(held.handle, purpose)
            except CredentialExpired:
                held = await self._issue(purpose, principal, scope)
                secret = await self._service.resolve(held.handle, purpose)
        if self._resolved is not None:
            self._resolved.add(secret.material)
        return secret

    def held(self) -> tuple[ShortLivedCredential, ...]:
        return tuple(self._held.values())

    async def revoke_all(self) -> int:
        held, self._held = list(self._held.values()), {}
        if self._resolved is not None:
            self._resolved.clear()
        for credential in held:
            await self._service.revoke(credential.handle)
        return len(held)


__all__ = ["CredentialLeases"]
