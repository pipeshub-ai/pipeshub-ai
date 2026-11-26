"""``HandleCredentialService``: purpose-bound, short-lived handles over secret sources.

The handle table lives in this process: a handle is useful only to the
process that issued it, for the purpose it was issued for, until it expires
or is revoked. Sources hold the secrets; a source that keeps versions lets an
in-flight handle resolve the version it was issued against after a rotation.

Every issuance, revocation and rotation is logged with the handle, the
purpose and the scope; a refusal once at warning; never the material.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from harness_core.credentials.errors import (
    CredentialExpired,
    CredentialKindUnknown,
    CredentialPurposeMismatch,
    CredentialRefused,
    CredentialRevoked,
    CredentialTenantMismatch,
    CredentialTtlInvalid,
    CredentialUnavailable,
    CredentialUnknown,
)
from harness_core.credentials.types import (
    HANDLE_PREFIX,
    MAX_TTL_S,
    CredentialPrincipal,
    CredentialPurpose,
    CredentialScope,
    Secret,
    ShortLivedCredential,
)
from harness_core.ids import RandomIds
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from pydantic import JsonValue

    from harness_core.clock import Clock
    from harness_core.credentials.ports import SecretSource
    from harness_core.ids import IdSource


@dataclass(slots=True)
class _Issued:
    credential: ShortLivedCredential
    principal: CredentialPrincipal
    revoked: bool = False


class HandleCredentialService:
    def __init__(
        self,
        sources: Mapping[str, SecretSource],
        clock: Clock,
        *,
        ids: IdSource | None = None,
        max_ttl_s: int = MAX_TTL_S,
        log: HarnessLog | None = None,
    ) -> None:
        self._sources = dict(sources)
        self._clock = clock
        self._ids = ids or RandomIds()
        self._max_ttl_s = max_ttl_s
        self._log = log or HarnessLog.named("harness_core.credentials")
        self._issued: dict[str, _Issued] = {}

    def _source(self, scope: CredentialScope) -> SecretSource:
        source = self._sources.get(scope.kind)
        if source is None:
            raise CredentialKindUnknown(f"no source serves credentials of kind {scope.kind!r}")
        return source

    def _refuse(self, refusal: CredentialRefused, **fields: str | int | None) -> CredentialRefused:
        self._log.warning("credential.refused", reason=refusal.reason_code, **fields)
        return refusal

    def _forget_stale(self, now: datetime) -> None:
        # Kept one TTL past expiry so a late use reads "expired", not "unknown".
        horizon = now - timedelta(seconds=self._max_ttl_s)
        for handle in [h for h, i in self._issued.items() if i.credential.expires_at <= horizon]:
            del self._issued[handle]

    async def issue(
        self,
        purpose: CredentialPurpose,
        principal: CredentialPrincipal,
        scope: CredentialScope,
        *,
        ttl_s: int,
    ) -> ShortLivedCredential:
        where: dict[str, str | int | None] = {
            "orgId": purpose.tenant,
            "runId": purpose.run_id,
            "attemptId": purpose.attempt_id,
            "tool": purpose.tool,
            "kind": scope.kind,
        }
        if not 0 < ttl_s <= self._max_ttl_s:
            raise self._refuse(CredentialTtlInvalid(f"ttl must be within 1..{self._max_ttl_s} s"), ttl=ttl_s, **where)
        if principal.tenant != purpose.tenant:
            raise self._refuse(CredentialTenantMismatch("the principal belongs to another tenant"), **where)
        try:
            source = self._source(scope)
        except CredentialKindUnknown as exc:
            raise self._refuse(exc, **where) from None
        try:
            current = await source.read(purpose.tenant, scope)
        except CredentialRefused as exc:
            raise self._refuse(exc, **where) from None
        if current is None:
            raise self._refuse(CredentialUnavailable("no usable secret for this scope"), **where)
        now = self._clock.now()
        self._forget_stale(now)
        credential = ShortLivedCredential(
            handle=self._ids.new_id(HANDLE_PREFIX),
            purpose=purpose,
            scope=scope,
            version=current.version,
            expires_at=now + timedelta(seconds=ttl_s),
        )
        self._issued[credential.handle] = _Issued(credential, principal)
        self._log.info(
            "credential.issued",
            handle=credential.handle,
            principal=principal.subject,
            version=credential.version,
            expiresAt=credential.expires_at.isoformat(),
            **where,
        )
        return credential

    async def resolve(self, handle: str, purpose: CredentialPurpose) -> Secret:
        where: dict[str, str | int | None] = {
            "orgId": purpose.tenant,
            "runId": purpose.run_id,
            "attemptId": purpose.attempt_id,
            "tool": purpose.tool,
            "handle": handle,
        }
        issued = self._issued.get(handle)
        if issued is None:
            raise self._refuse(CredentialUnknown("no such credential handle"), **where)
        credential = issued.credential
        if credential.purpose != purpose:
            raise self._refuse(CredentialPurposeMismatch("the handle was issued for another purpose"), **where)
        if issued.revoked:
            raise self._refuse(CredentialRevoked("the credential was revoked"), **where)
        if self._clock.now() >= credential.expires_at:
            raise self._refuse(CredentialExpired("the credential expired"), **where)
        try:
            secret = await self._source(credential.scope).read(purpose.tenant, credential.scope, credential.version)
        except CredentialRefused as exc:
            # A source that says the secret was revoked revokes the handle with it: the
            # next resolve refuses without asking the source again.
            if isinstance(exc, CredentialRevoked):
                issued.revoked = True
            raise self._refuse(exc, **where) from None
        if secret is None:
            raise self._refuse(CredentialUnavailable("the secret behind the handle is gone"), **where)
        self._log.debug("credential.resolved", kind=credential.scope.kind, version=secret.version, **where)
        return secret

    async def revoke(self, handle: str) -> None:
        issued = self._issued.get(handle)
        if issued is None or issued.revoked:
            return
        issued.revoked = True
        purpose = issued.credential.purpose
        self._log.info(
            "credential.revoked",
            orgId=purpose.tenant,
            runId=purpose.run_id,
            attemptId=purpose.attempt_id,
            tool=purpose.tool,
            handle=handle,
        )

    async def rotate(self, tenant: str, scope: CredentialScope, material: JsonValue) -> str:
        try:
            source = self._source(scope)
            version = await source.write(tenant, scope, material)
        except CredentialRefused as exc:
            raise self._refuse(exc, orgId=tenant, kind=scope.kind) from None
        self._log.info("credential.rotated", orgId=tenant, kind=scope.kind, version=version)
        return version


__all__ = ["HandleCredentialService"]
