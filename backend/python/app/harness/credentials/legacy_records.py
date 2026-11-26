"""The legacy toolset and MCP credential routes' writes, through one seam (CPD-8).

``CONTROL_PLANE_CREDENTIALS`` off: the old paths, as before. On: no credential is written to an old
path any more; a save becomes a new version of the owner's binding (written by Node's binder, audited)
and a disconnect revokes it (a tombstone, final for every reader). A narrowing (a disconnect, an
admin's deauthorisation) is also applied to the old record, so the legacy pages that still read it
agree and a revert of the migration cannot revive it. A cohesion check
(``tests/cohesion/test_cpd8_legacy_credential_writes.py``) keeps every route writing through here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, cast

from app.harness.credentials.binding_writer import ImportRequest, Material
from app.harness.credentials.migration import owner_ref, record_expiry_ms, record_mode
from app.harness.credentials.read_through import credentials_switch_on, record_digest

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.harness.credentials.binding_writer import BindingWriter

Kind = Literal["toolset", "mcp"]


def old_path(kind: Kind, instance_id: str, owner_id: str) -> str:
    if kind == "mcp":
        from app.agents.constants.mcp_server_constants import get_mcp_credentials_path

        return get_mcp_credentials_path(instance_id, owner_id)
    from app.agents.constants.toolset_constants import get_toolset_config_path

    return get_toolset_config_path(instance_id, owner_id)


class LegacyCredentialRecords:
    def __init__(self, config: Any, writer: Callable[[], Awaitable[BindingWriter]]) -> None:  # noqa: ANN401
        self._config = config
        self._writer = writer

    async def load(
        self, kind: Kind, org_id: str, instance_id: str, owner_id: str, *, raise_on_error: bool = False,
    ) -> dict[str, Any] | None:
        """The owner's current record: the binding's, with the switch on and a binding there; else the old path's.
        ``raise_on_error``: an unreadable store raises instead of reading as no record, for a caller about to
        write the record back (an outage must not answer "authenticate first")."""
        path = old_path(kind, instance_id, owner_id)
        stored: object = await self._config.get_config(path, default=None, use_cache=False, raise_on_error=raise_on_error)
        if credentials_switch_on():
            from app.harness.credentials.bindings import BindingDocuments
            from app.harness.credentials.read_through import BindingReadThrough
            from app.harness.credentials.vault import VaultSecretStore
            from harness_core.clock import SystemClock
            from harness_core.credentials.types import CredentialScope

            through = BindingReadThrough(BindingDocuments(VaultSecretStore(self._config, SystemClock())))
            bound = await through.read(org_id, CredentialScope(kind, f"{instance_id}/{owner_id}"), instance_id, owner_id, stored)
            if bound is not None and isinstance(bound.material, dict):
                return cast("dict[str, Any]", bound.material)
        return cast("dict[str, Any]", stored) if isinstance(stored, dict) else None

    async def save(
        self,
        kind: Kind,
        org_id: str,
        instance_id: str,
        owner_id: str,
        record: dict[str, Any],
        *,
        user_id: str | None = None,
        narrowing: bool = False,
    ) -> bool:
        """Stores ``record`` as ``owner_id``'s credential for the instance; ``False`` when the store refused it.
        ``narrowing``: the record only withdraws (``isAuthenticated`` off), so the old record follows it."""
        path = old_path(kind, instance_id, owner_id)
        if not credentials_switch_on() or narrowing:
            written = bool(await self._config.set_config(path, record))
            if not credentials_switch_on():
                return written
        # An outage must not record "no old record": the read-through would then trust a copy it cannot compare.
        current: object = await self._config.get_config(path, default=None, use_cache=False, raise_on_error=True)
        observed = record_digest(cast("dict[str, Any]", current)) if isinstance(current, dict) else None
        _ = await (await self._writer()).import_record(
            org_id,
            ImportRequest(
                installation=instance_id,
                owner=owner_ref(record, owner_id),
                mode=record_mode(kind, record),
                kind=kind,
                provider=cast("str | None", record.get("toolsetType")),
                material=Material("record", record, record_expiry_ms(kind, record)),
                source_path=None,
                source_digest=None,
                observed_path=path if observed is not None else None,
                observed_digest=observed,
            ),
            user_id=user_id,
        )
        return True

    async def remove(self, kind: Kind, org_id: str, instance_id: str, owner_id: str, *, user_id: str | None = None) -> None:
        """Disconnects ``owner_id`` (a user or an agent) from the instance."""
        if credentials_switch_on():
            writer = await self._writer()
            for owner in (f"user:{owner_id}", f"agent:{owner_id}"):
                _ = await writer.revoke_owner(org_id, instance_id, owner, user_id=user_id)
        _ = await self._config.delete_config(old_path(kind, instance_id, owner_id))


_PROCESS: dict[int, tuple[object, LegacyCredentialRecords]] = {}


def legacy_records(config: Any) -> LegacyCredentialRecords:  # noqa: ANN401
    """The process's seam for this configuration service: Node's binder, reached once a write needs it."""
    found = _PROCESS.get(id(config))
    if found is not None and found[0] is config:
        return found[1]
    held: list[BindingWriter] = []

    async def writer() -> BindingWriter:
        if not held:
            from app.harness.credentials.binding_writer import node_binding_writer

            held.append(await node_binding_writer(config))
        return held[0]

    seam = LegacyCredentialRecords(config, writer)
    _PROCESS[id(config)] = (config, seam)
    return seam


__all__ = ["LegacyCredentialRecords", "legacy_records", "old_path"]
