"""Keeping the graph in step with a Snowflake account from one sync to the next.

Mixed into ``SnowflakeConnector``. Each sync reads the filtered catalog and
writes every listed object. The engine-neutral half (renames,
removals, ``sync_outcome``) is ``SqlSyncLifecycleMixin``; this adds
Snowflake's groups and its per-database saved state. Decisions are in
``catalog_sync``; this is the I/O.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.sql.sync_lifecycle import (
    LOOKUP_ATTEMPTS,
    RECORDS_REMOVED_PER_BATCH,
    SqlSyncLifecycleMixin,
    restructured_fqns,
)
from app.connectors.core.sql.sync_lifecycle import column_fqn as _column_fqn
from app.connectors.sources.snowflake.sync_state import DatabaseState

if TYPE_CHECKING:
    from app.connectors.sources.snowflake.catalog_state import SnowflakeTableState
    from app.models.entities import RecordGroupType

__all__ = [
    "LOOKUP_ATTEMPTS",
    "RECORDS_REMOVED_PER_BATCH",
    "SyncLifecycleMixin",
    "_column_fqn",
    "checkpoint_after_moves",
    "restructured_fqns",
]


class SyncLifecycleMixin(SqlSyncLifecycleMixin):
    async def _stored_groups(self, group_type: RecordGroupType) -> dict[str, str]:
        """External id -> node id of this connector's groups of a type."""
        async with self.data_store_provider.transaction() as tx_store:
            groups = await tx_store.get_nodes_by_filters(
                collection=CollectionNames.RECORD_GROUPS.value,
                filters={"connectorId": self.connector_id, "groupType": group_type.value},
            )
        return {
            g["externalGroupId"]: str(g.get("id") or g.get("_key"))
            for g in groups if g.get("externalGroupId") and (g.get("id") or g.get("_key"))
        }

    async def _remove_groups(self, group_type: RecordGroupType, external_ids: list[str]) -> set[str]:
        """Delete these groups (only the groups: their records are removed first); returns those deleted."""
        if not external_ids:
            return set()
        try:
            node_ids = await self._stored_groups(group_type)
        except Exception as e:
            self.sync_stats.errors += 1
            self.logger.warning(f"Could not list {group_type.value} groups to remove: {e}")
            return set()
        deleted: set[str] = set()
        deleted_node_ids: list[str] = []
        try:
            for external_id in external_ids:
                if external_id not in node_ids:
                    deleted.add(external_id)  # already gone: deleting it would report a failure
                    continue
                if await self.data_entities_processor.on_record_group_deleted(external_id, self.connector_id):
                    deleted.add(external_id)
                    deleted_node_ids.append(node_ids[external_id])
                else:
                    self.sync_stats.errors += 1
        finally:
            # A group deleted is not listed again, so its search entries go now.
            if deleted_node_ids:
                await self.data_entities_processor.on_sql_record_groups_deleted(deleted_node_ids, self.connector_id)
        return deleted


def checkpoint_after_moves(
    stored: dict[str, DatabaseState],
    moved: dict[str, str],
    current: dict[str, SnowflakeTableState],
    record_ids: dict[str, str],
) -> dict[str, DatabaseState]:
    """The stored state with each table about to move under its new name.

    Saved before records move, so a sync cut off after it never decides the
    move again; ``renamed_from`` has the next sync finish the rename and
    ``moved_record_id`` tells whether the records moved.
    """
    states = {database: state.model_copy(deep=True) for database, state in stored.items()}
    entries: dict[str, SnowflakeTableState] = {}
    for state in states.values():
        for old_fqn in moved:
            if old_fqn in state.tables:
                entries[old_fqn] = state.tables.pop(old_fqn)
    for old_fqn, new_fqn in moved.items():
        if old_fqn not in entries or new_fqn not in current:
            continue
        target = current[new_fqn]
        states.setdefault(target.database, DatabaseState()).tables[new_fqn] = entries[old_fqn].model_copy(update={
            "database": target.database,
            "schema_name": target.schema_name,
            "table_name": target.table_name,
            "renamed_from": old_fqn,
            "moved_record_id": record_ids.get(old_fqn, ""),
        })
    return states
