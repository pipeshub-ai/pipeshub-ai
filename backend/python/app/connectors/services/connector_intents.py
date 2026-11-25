"""Durable per-connector intents in the KV store.

A connector or KB delete owes cleanup that must survive a crash or a lost
event after its graph rows are gone, when nothing else remembers the connector
existed. The deleting service records an intent first, the cleanup clears it,
and a leader-elected loop runs whatever is left behind, backing off per
attempt. Each kind of cleanup gets its own directory.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames
from app.core.org_context import spawn_detached, system
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider


def owner_of(item: dict[str, Any]) -> object:
    """The org a stored app or intent belongs to: compared with the caller's, never acted as."""
    return item.get("orgId")


def held_by_another_org(app: dict[str, Any], org_id: str) -> bool:
    """Whether ``app`` is another org's: a store that does not scope reads by org answers it for any caller."""
    return owner_of(app) not in (None, "", org_id)


async def exists_in_any_org(graph_provider: IGraphDBProvider, connector_id: str) -> bool:
    """Whether any org holds the connector: a store that scopes reads by org answers None for another org's. Only that
    it exists is told. Raises when the store cannot answer."""
    async def read() -> bool:
        with system("connector_exists"):
            return await graph_provider.get_document(
                connector_id, CollectionNames.APPS.value, raise_on_error=True
            ) is not None

    # Detached: the caller may have an org bound, which a system scope may not be nested in.
    return await spawn_detached(read(), name="connector-exists")


def _merged(older: dict[str, Any] | None, newer: dict[str, Any] | None) -> dict[str, Any]:
    """``newer``, with the record groups only ``older`` names."""
    merged = {**(newer or older or {})}
    lists = [(item or {}).get("recordGroupIds") for item in (newer, older)]
    groups = [group for groups in lists if isinstance(groups, list) for group in groups]
    if groups:
        merged["recordGroupIds"] = list(dict.fromkeys(groups))
    return merged


class ConnectorIntentStore:
    def __init__(self, directory: str, error_type: type[Exception], what: str) -> None:
        self.directory = directory
        self.error_type = error_type
        self.what = what

    def key(self, org_id: str, connector_id: str) -> str:
        """One key per org and connector, so no write or delete for one org can reach another's intent."""
        return f"{self.directory}{org_id}/{connector_id}"

    def _unscoped_key(self, connector_id: str) -> str:
        """Where earlier versions kept the intent: read and cleared, never written."""
        return f"{self.directory}{connector_id}"

    async def read(self, config_service: ConfigurationService, org_id: str, connector_id: str) -> dict[str, Any] | None:
        """The org's intent, whichever version recorded it. Raises when the store cannot answer."""
        found = None
        for key in (self._unscoped_key(connector_id), self.key(org_id, connector_id)):
            value = await config_service.get_config(key, use_cache=False, raise_on_error=True)
            if isinstance(value, dict) and owner_of(value) == org_id:
                found = _merged(found, value)
        return found

    async def record(
        self,
        config_service: ConfigurationService,
        *,
        org_id: str,
        connector_id: str,
        now_ms: int | None = None,
        **fields: Any,  # noqa: ANN401
    ) -> None:
        """Raises ``error_type`` when the KV store refuses the write."""
        intent = {
            "orgId": org_id,
            "connectorId": connector_id,
            **fields,
            "requestedAt": get_epoch_timestamp_in_ms() if now_ms is None else now_ms,
        }
        if not await config_service.set_config(self.key(org_id, connector_id), intent):
            raise self.error_type(f"could not record {self.what} for connector {connector_id}")

    async def clear(self, config_service: ConfigurationService, org_id: str, connector_id: str) -> bool:
        """A failed read or delete is returned, not raised: the intent then runs again,
        and every cleanup behind one is idempotent."""
        # Absent for deletes made before intents existed and for ones already
        # settled; Redis reports deleting a missing key as a failure.
        cleared = True
        for key in (self._unscoped_key(connector_id), self.key(org_id, connector_id)):
            try:
                value = await config_service.get_config(key, use_cache=False, raise_on_error=True)
                # An earlier version's key may hold another org's intent: that one is left. Only a replica of that
                # version can put one there between this read and the delete.
                if isinstance(value, dict) and owner_of(value) == org_id:
                    cleared = bool(await config_service.delete_config(key)) and cleared
            except Exception:
                cleared = False
        return cleared

    async def reschedule(
        self, config_service: ConfigurationService, intent: dict[str, Any], *, next_attempt_at: int,
    ) -> bool:
        """Count a failed attempt on ``intent`` and hold it until ``next_attempt_at``."""
        org_id, connector_id = str(owner_of(intent)), str(intent["connectorId"])
        # Raised, not None: what was added to the stored intent since the caller read it is kept.
        # An earlier version's entry is merged by the list, and one that cannot be read must not stop this.
        current = await config_service.get_config(self.key(org_id, connector_id), use_cache=False, raise_on_error=True)
        current = current if isinstance(current, dict) else None
        updated = {
            **_merged(current, intent),
            "attempts": int(intent.get("attempts") or 0) + 1, "nextAttemptAt": next_attempt_at,
        }
        # An earlier version's entry stays until the intent is cleared; the list merges the two.
        return bool(await config_service.set_config(self.key(org_id, connector_id), updated))

    async def list(self, config_service: ConfigurationService) -> list[dict[str, Any]]:
        """Every recorded intent, oldest first; malformed entries are skipped."""
        intents: dict[tuple[str, str], dict[str, Any]] = {}
        unscoped: list[dict[str, Any]] = []
        for key in await config_service.list_keys_in_directory(self.directory):
            value = await config_service.get_config(key, use_cache=False)
            if not isinstance(value, dict) or not owner_of(value) or not value.get("connectorId"):
                continue
            org_id, connector_id = str(owner_of(value)), str(value["connectorId"])
            if key == self.key(org_id, connector_id):
                intents[org_id, connector_id] = value
            elif key == self._unscoped_key(connector_id):
                unscoped.append(value)
        for value in unscoped:
            at = (str(owner_of(value)), str(value["connectorId"]))
            intents[at] = _merged(value, intents.get(at))
        return sorted(intents.values(), key=lambda i: (int(i.get("requestedAt") or 0), str(i["connectorId"])))
