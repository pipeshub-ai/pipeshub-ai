"""The old toolset and MCP credential paths read through the control plane's bindings (CPD-8).

With ``CONTROL_PLANE_CREDENTIALS`` on, a credential is read from its binding first: the migration
copied the record there, the legacy routes write through the binder and the token refresher renews
it there. A tombstone is final: a revoked binding never falls back to the old record. A copy the old
record changed after (a write before the flip that the migration has not copied yet) is not served:
the old record is, until the migration's next run copies it. Without a binding, the old record is read.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from app.utils.env_flags import CONTROL_PLANE_CREDENTIALS, env_flag
from harness_core.contracts.canonical_json import canonical_digest
from harness_core.credentials.errors import CredentialRevoked
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from app.harness.credentials.bindings import BindingDocuments
    from harness_core.credentials.types import CredentialScope, Secret

OWNER_KINDS = ("user", "agent")


def credentials_switch_on() -> bool:
    return env_flag(CONTROL_PLANE_CREDENTIALS)


def record_digest(record: object) -> str:
    """The digest the migration records for an old-path record (its ``source.digest``)."""
    return canonical_digest(record)


class BindingReadThrough:
    def __init__(self, documents: BindingDocuments, log: HarnessLog | None = None) -> None:
        self._documents = documents
        self._log = log or HarnessLog.named("app.harness.credentials.bindings")

    async def read(
        self, tenant: str, scope: CredentialScope, installation: str, owner: str, old_record: object
    ) -> Secret | None:
        """The binding's current version for ``owner`` (an id of a user or an agent); ``None``: read the old record."""
        for kind in OWNER_KINDS:
            path, document = await self._documents.owner_document(tenant, installation, f"{kind}:{owner}")
            if not document:
                continue
            if document.get("revoked"):
                raise CredentialRevoked("the connection was revoked")
            meta = document.get("binding")
            source = cast("dict[str, Any]", meta).get("source") if isinstance(meta, dict) else None
            if (
                isinstance(source, dict)
                and old_record is not None
                and record_digest(old_record) != cast("dict[str, Any]", source).get("digest")
            ):
                self._log.warning(
                    "credential.binding_stale", orgId=tenant, kind=scope.kind, reasonCode="old_record_changed"
                )
                return None
            return await self._documents.vault.read_at(path, tenant, scope)
        return None


__all__ = ["BindingReadThrough", "credentials_switch_on", "record_digest"]
