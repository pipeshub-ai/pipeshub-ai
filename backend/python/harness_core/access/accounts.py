"""In-memory ``ModelAccountStore`` that is also the ``SecretSource`` of its accounts (P2.4b).

Versions keep counting across a removal, so a handle pinned to a version of a
removed key can never resolve a key added later under the same account.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

from harness_core.access.types import (
    AccountKind,
    AccountRef,
    ModelAccount,
    account_owner,
    last_four,
)
from harness_core.credentials.errors import CredentialReadOnly, CredentialRevoked
from harness_core.credentials.types import MAX_TTL_S, Secret

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from pydantic import JsonValue

    from harness_core.clock import Clock
    from harness_core.credentials.types import CredentialScope


@dataclass(slots=True)
class _Version:
    number: int
    material: JsonValue
    superseded_at: datetime | None = None


@dataclass(slots=True)
class _Record:
    kind: AccountKind
    account: ModelAccount | None = None
    versions: list[_Version] = field(default_factory=list[_Version])
    last: int = 0


class InMemoryModelAccountStore:
    def __init__(self, clock: Clock, *, retention_s: int = MAX_TTL_S) -> None:
        self._clock = clock
        self._retention = timedelta(seconds=retention_s)
        self._records: dict[tuple[str, str], _Record] = {}

    def _live(self, record: _Record) -> list[_Version]:
        horizon = self._clock.now() - self._retention
        record.versions[:] = [v for v in record.versions if v.superseded_at is None or v.superseded_at > horizon]
        return record.versions

    async def put(
        self,
        tenant: str,
        ref: AccountRef,
        provider: str,
        material: Mapping[str, JsonValue],
        *,
        secret_hint: str,
        label: str | None,
        actor: str,
    ) -> ModelAccount:
        owner = account_owner(ref)
        now = self._clock.now()
        record = self._records.setdefault((tenant, ref.account_id), _Record(ref.kind))
        versions = self._live(record)
        if versions:
            versions[-1].superseded_at = now
        record.last += 1
        versions.append(_Version(record.last, copy.deepcopy(dict(material))))
        previous = record.account
        record.account = ModelAccount(
            ref=ref,
            owner=owner,
            provider=provider,
            label=label,
            last4=last_four(secret_hint),
            version=str(record.last),
            created_at=previous.created_at if previous is not None else now,
            rotated_at=now,
        )
        return record.account

    async def remove(self, tenant: str, ref: AccountRef, *, actor: str) -> bool:
        record = self._records.get((tenant, ref.account_id))
        if record is None or record.account is None:
            return False
        record.account, record.versions = None, []
        return True

    async def account(self, tenant: str, ref: AccountRef) -> ModelAccount | None:
        _ = account_owner(ref)
        record = self._records.get((tenant, ref.account_id))
        return None if record is None else record.account

    async def accounts(self, tenant: str, owner: str) -> tuple[ModelAccount, ...]:
        found = [
            r.account
            for (t, _), r in self._records.items()
            if t == tenant and r.account is not None and r.account.owner == owner
        ]
        return tuple(sorted(found, key=lambda a: (a.created_at, a.ref.account_id)))

    async def erase(self, tenant: str, owner: str | None = None) -> int:
        doomed = [
            key
            for key, record in self._records.items()
            if key[0] == tenant and (owner is None or account_owner(AccountRef(record.kind, key[1])) == owner)
        ]
        for key in doomed:
            del self._records[key]
        return len(doomed)

    async def read(self, tenant: str, scope: CredentialScope, version: str | None = None) -> Secret | None:
        record = self._records.get((tenant, scope.name))
        if record is None:
            return None
        if record.account is None:
            raise CredentialRevoked("the model account was removed")
        versions = self._live(record)
        found = versions[-1] if version is None else next((v for v in versions if str(v.number) == version), None)
        return None if found is None else Secret(str(found.number), copy.deepcopy(found.material))

    async def write(self, tenant: str, scope: CredentialScope, material: JsonValue) -> str:
        raise CredentialReadOnly("model accounts are written through ModelAccountStore.put")


__all__ = ["InMemoryModelAccountStore"]
