"""``ModelAccountStore`` over the vault (P2.4b): users' own keys and org cloud accounts.

Node's model-account routes write the same documents in production
(``modules/agent-harness/adapters/configuration/model-account-vault.ts``); this
adapter is the Python reader of their metadata, and a writer for hosts and
tests that have no Node. The secret is read through the vault as kind
``vault`` with the account id as its name.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from app.harness.credentials.vault import VAULT_KIND
from harness_core.access.types import (
    ORG_OWNER,
    AccountKind,
    AccountRef,
    ModelAccount,
    ValidationState,
    account_owner,
    last_four,
)
from harness_core.credentials.types import CredentialScope

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from app.harness.credentials.vault import VaultSecretStore
    from harness_core.clock import Clock

# The vault directory of each owner's accounts.
USERS_DIRECTORY = "users"
CLOUD_DIRECTORY = "cloud"


def _directory(owner: str) -> str:
    return CLOUD_DIRECTORY if owner == ORG_OWNER else f"{USERS_DIRECTORY}/{owner}"


def _ref(name: str) -> AccountRef:
    kind = AccountKind.ORG_CLOUD if name.startswith(f"{CLOUD_DIRECTORY}/") else AccountKind.USER_API_KEY
    return AccountRef(kind, name)


def _account(ref: AccountRef, document: Mapping[str, Any]) -> ModelAccount | None:
    meta = document.get("account")
    if document.get("revoked") or not document.get("versions") or not isinstance(meta, dict):
        return None
    fields = cast("dict[str, Any]", meta)
    validation: ValidationState = "validated" if fields.get("validation") == "validated" else "unvalidated"
    return ModelAccount(
        ref=ref,
        owner=account_owner(ref),
        provider=str(fields.get("provider") or ""),
        label=cast("str | None", fields.get("label")),
        last4=str(fields.get("last4") or ""),
        version=str(document.get("lastVersion")),
        created_at=datetime.fromisoformat(str(fields["createdAt"])),
        rotated_at=datetime.fromisoformat(str(fields["rotatedAt"])),
        validation=validation,
    )


class VaultModelAccountStore:
    def __init__(self, vault: VaultSecretStore, clock: Clock) -> None:
        self._vault = vault
        self._clock = clock

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
        _ = account_owner(ref)
        now = self._clock.now().isoformat()
        current = await self._vault.document(tenant, ref.account_id)
        previous = current.get("account") if not current.get("revoked") else None
        created = cast("dict[str, Any]", previous) if isinstance(previous, dict) else {"createdAt": now, "createdBy": actor}
        fields: dict[str, JsonValue] = {
            "account": {
                "kind": ref.kind.value,
                "provider": provider,
                "label": label,
                "last4": last_four(secret_hint),
                "createdAt": created["createdAt"],
                "createdBy": created.get("createdBy", actor),
                "rotatedAt": now,
                "rotatedBy": actor,
                "validation": "unvalidated",
            }
        }
        _ = await self._vault.write(tenant, CredentialScope(VAULT_KIND, ref.account_id), dict(material), fields=fields)
        stored = await self.account(tenant, ref)
        if stored is None:
            raise RuntimeError("the model account was not stored")
        return stored

    async def remove(self, tenant: str, ref: AccountRef, *, actor: str) -> bool:
        _ = account_owner(ref)
        return await self._vault.revoke(tenant, ref.account_id, by=actor)

    async def account(self, tenant: str, ref: AccountRef) -> ModelAccount | None:
        _ = account_owner(ref)
        return _account(ref, await self._vault.document(tenant, ref.account_id))

    async def accounts(self, tenant: str, owner: str) -> tuple[ModelAccount, ...]:
        found: list[ModelAccount] = []
        for name in await self._vault.names(tenant, _directory(owner)):
            ref = _ref(name)
            account = _account(ref, await self._vault.document(tenant, name))
            if account is not None and account.owner == owner:
                found.append(account)
        return tuple(sorted(found, key=lambda a: (a.created_at, a.ref.account_id)))

    async def erase(self, tenant: str, owner: str | None = None) -> int:
        return await self._vault.erase(tenant, None if owner is None else _directory(owner))


__all__ = ["CLOUD_DIRECTORY", "USERS_DIRECTORY", "VaultModelAccountStore"]
