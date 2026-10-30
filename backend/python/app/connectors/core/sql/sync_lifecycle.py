"""Keeping the graph in step with a SQL source from one sync to the next, for any engine.

Mixed into a SQL connector after its catalog is diffed: it follows renamed
tables and columns to their new names, removes what is gone with everything
built from it, and records what changed in ``sync_outcome`` for whatever
runs after the sync. The decisions are the engine's; this is the I/O.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.sql.record_moves import Moves, hold_renames, rename_updates
from app.models.entities import Record, RecordType

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Collection, Mapping
    from logging import Logger

    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )
    from app.connectors.core.base.data_store.data_store import DataStoreProvider
    from app.connectors.core.sql.catalog_diff import CatalogDiff
    from app.connectors.core.sql.sync_outcome import SyncOutcome

# Records removed per cascade; each batch is one delete and one
# on_sql_records_deleted call.
RECORDS_REMOVED_PER_BATCH = 200
# Tries at resolving a restructured table's record id after the save.
LOOKUP_ATTEMPTS = 3


def column_fqn(table_fqn: str, column_name: str) -> str:
    """A column's id, for every engine: the table's id, a dot, the column's name as it is."""
    return f"{table_fqn}.{column_name}"


class SqlSyncLifecycleMixin:
    connector_id: str
    logger: Logger
    data_entities_processor: DataSourceEntitiesProcessor
    data_store_provider: DataStoreProvider
    sync_outcome: SyncOutcome
    sync_stats: Any
    # Tables whose records this sync wrote: only their restructuring counts.
    _written_tables: set[str]

    # --- outcome ------------------------------------------------------------

    async def _resolve_restructured(self) -> None:
        """Turn the restructured tables' FQNs into the ids of the records saved for them.

        Looked up after the save: a table saved after one that references it
        takes over the stand-in that table created, and with it its id. Only
        tables written this sync count: one that was not keeps its saved
        state, so the next sync finds it restructured again.
        """
        for fqn in sorted(self.sync_outcome.restructured_tables & self._written_tables):
            record = None
            for attempt in range(1, LOOKUP_ATTEMPTS + 1):
                try:
                    record = await self.data_entities_processor.get_record_by_external_id(
                        connector_id=self.connector_id, external_record_id=fqn, raise_on_error=True,
                    )
                    break
                except Exception as e:
                    if attempt == LOOKUP_ATTEMPTS:
                        self.sync_stats.errors += 1
                        self.logger.error(f"Could not look up restructured table {fqn}: {e}")
                    else:
                        await asyncio.sleep(attempt)
            if record and record.id:
                self.sync_outcome.restructured.add(record.id)

    async def _announce_renames(self) -> None:
        """Tell the processor which tables were renamed; report them as restructured when that fails."""
        renamed = sorted(self.sync_outcome.renamed - self.sync_outcome.removed)
        if not renamed:
            return
        try:
            await self.data_entities_processor.on_sql_records_renamed(renamed, self.connector_id)
        except Exception as e:
            self.logger.warning(f"Could not report {len(renamed)} renamed table(s); reporting them as restructured: {e}")
            self.sync_outcome.restructured.update(renamed)

    # --- removals -----------------------------------------------------------

    async def _remove_records(self, record_ids: list[str], *, table_record_id: str | None = None) -> None:
        """Delete records with everything built from them; raises when the delete fails."""
        await self._note_tables_to_relate_again(record_ids, table_record_id=table_record_id)
        removed = await self.data_entities_processor.on_sql_records_deleted(
            record_ids, self.connector_id, table_record_id=table_record_id,
        )
        self.sync_outcome.removed.update(removed)

    async def _note_tables_to_relate_again(self, record_ids: list[str], *, table_record_id: str | None) -> None:
        """Note the tables whose relations depended on these records, before they go. Best effort."""
        try:
            sources = await self.data_entities_processor.sql_tables_to_relate_again(
                [table_record_id] if table_record_id else record_ids,
                column_ids=record_ids if table_record_id else None,
            )
        except Exception as e:
            self.logger.warning(f"Could not find the tables related through {len(record_ids)} removed record(s): {e}")
            return
        self.sync_outcome.relations_only.update(sources)

    async def _remove_by_external_ids(self, external_ids: list[str]) -> set[str]:
        """Remove the records stored under these ids; returns the ids handled.

        An id whose lookup fails is not handled, so it is tried again next sync:
        a failed lookup is no answer that nothing is stored.
        """
        handled: set[str] = set()
        record_ids: dict[str, str] = {}
        for external_id in external_ids:
            try:
                record = await self.data_entities_processor.get_record_by_external_id(
                    connector_id=self.connector_id, external_record_id=external_id, raise_on_error=True,
                )
            except Exception as e:
                self.sync_stats.errors += 1
                self.logger.warning(f"Could not look up {external_id} to remove it: {e}")
                continue
            if record and record.id:
                record_ids[external_id] = record.id
            else:
                handled.add(external_id)
        handled |= await self._remove_in_batches(sorted(record_ids.items()))
        return handled

    async def _remove_in_batches(self, items: list[tuple[str, str]]) -> set[str]:
        """Remove ``(external id, record id)`` pairs in batches; returns the external ids removed."""
        removed: set[str] = set()
        for start in range(0, len(items), RECORDS_REMOVED_PER_BATCH):
            chunk = items[start:start + RECORDS_REMOVED_PER_BATCH]
            try:
                await self._remove_records([record_id for _, record_id in chunk])
                removed.update(external_id for external_id, _ in chunk)
            except Exception as e:
                self.sync_stats.errors += 1
                self.logger.warning(f"Could not remove {len(chunk)} record(s): {e}")
        return removed

    async def _sweep_records(
        self, record_type: RecordType, listed: set[str], unreadable: Callable[[str], bool] | None = None,
    ) -> set[str]:
        """Without a saved state: remove this connector's records of a type that nothing lists.

        Placeholders (stand-ins for referenced tables) and records under a
        scope that could not be listed (``unreadable``) stay. Returns the ids that could not be
        removed, for the saved state to keep so the next sync tries again.
        """
        try:
            records = await self.data_entities_processor.get_records_by_record_type(self.connector_id, record_type)
        except Exception as e:
            self.sync_stats.errors += 1
            self.logger.warning(f"Could not list {record_type.value} records to remove stale ones: {e}")
            return set()
        stale = [
            (r.external_record_id, r.id) for r in records
            if r.id and r.external_record_id not in listed
            and not getattr(r, "is_placeholder", False)
            and not (unreadable and unreadable(r.external_record_id or ""))
        ]
        if not stale:
            return set()
        self.logger.info(f"Removing {len(stale)} {record_type.value} record(s) nothing lists any more")
        return {external_id for external_id, _ in stale} - await self._remove_in_batches(sorted(stale))

    # --- renames ------------------------------------------------------------

    async def _move_renamed_tables(
        self,
        renamed: dict[str, str],
        *,
        blocked: set[str],
        checkpoint: Callable[[dict[str, str], dict[str, str]], Awaitable[bool]],
        expected_ids: dict[str, str] | None = None,
    ) -> tuple[dict[str, str], Moves]:
        """Move renamed tables' records (and their columns') to the new names.

        Returns the renames done and the records moved. A table waits when the
        name it moves into is not free (see ``hold_renames``). ``checkpoint``
        saves the moves, with the records' ids, before anything is written: a
        rename then never runs twice, and one cut off is finished by
        ``_finish_interrupted_moves``. When it fails nothing moves.
        ``expected_ids`` skips an old name whose record is not the one expected.
        """
        moves: Moves = {}
        unmoved: set[str] = set()
        for old_fqn, new_fqn in sorted(renamed.items()):
            try:
                async with self.data_store_provider.transaction() as tx_store:
                    table = await tx_store.get_record_by_external_id(self.connector_id, old_fqn, raise_on_error=True)
                    if table is None or (expected_ids and table.id != expected_ids.get(old_fqn)):
                        unmoved.add(new_fqn)
                        continue
                    columns = await tx_store.get_records_by_parent(
                        self.connector_id, old_fqn, RecordType.SQL_COLUMN.value,
                    )
                moves[old_fqn] = (table, columns)
            except Exception as e:
                self.sync_stats.errors += 1
                unmoved.add(new_fqn)
                self.logger.error(f"Could not look up renamed table {old_fqn}: {e}")

        hold_renames(renamed, moves, unmoved, blocked)
        if not moves:
            return {}, {}
        if not await checkpoint(
            {old: renamed[old] for old in moves}, {old: table.id for old, (table, _) in moves.items()},
        ):
            self.logger.error(f"Could not save the sync state; {len(moves)} renamed table(s) wait for the next sync")
            return {}, {}

        for old_fqn in sorted(moves):
            new_fqn = renamed[old_fqn]
            if new_fqn in moves:
                continue  # left by a table that moves too
            try:
                await self._remove_occupant(new_fqn, moves[old_fqn][0])
            except Exception as e:
                self.sync_stats.errors += 1
                unmoved.add(new_fqn)
                moves.pop(old_fqn)
                self.logger.error(f"Could not free {new_fqn} for {old_fqn}: {e}")
        hold_renames(renamed, moves, unmoved, blocked)

        updates = rename_updates(moves, renamed, column_fqn)
        if updates:
            try:
                async with self.data_store_provider.transaction() as tx_store:
                    await tx_store.batch_update_nodes(updates, CollectionNames.RECORDS.value)
            except Exception as e:
                self.sync_stats.errors += 1
                self.logger.error(f"Could not move {len(moves)} renamed table(s): {e}")
                return {}, {}
        done = {old: renamed[old] for old in moves}
        for old_fqn, new_fqn in sorted(done.items()):
            self.logger.info(f"Table {old_fqn} is now {new_fqn}; kept its record")
        return done, moves

    async def _finish_interrupted_moves(self, stored: Mapping[str, Any]) -> None:
        """Finish the moves a run saved but did not write (it stopped between the two).

        ``stored`` are the saved table states, each with ``renamed_from`` and
        ``moved_record_id``. A move is done when the record under the
        new name is the one saved;
        otherwise the saved record is moved from the old name, if it is still there.
        """
        pending = {
            state.renamed_from: fqn for fqn, state in stored.items()
            if state.moved_record_id and state.renamed_from and state.renamed_from != fqn
        }
        if not pending:
            return
        expected = {old: stored[new].moved_record_id for old, new in pending.items()}
        unfinished: dict[str, str] = {}
        for old_fqn, new_fqn in sorted(pending.items()):
            try:
                async with self.data_store_provider.transaction() as tx_store:
                    current = await tx_store.get_record_by_external_id(self.connector_id, new_fqn, raise_on_error=True)
            except Exception as e:
                self.sync_stats.errors += 1
                self.logger.error(f"Could not check the move of {old_fqn} to {new_fqn}: {e}")
                continue
            if current is None or current.id != expected[old_fqn]:
                unfinished[old_fqn] = new_fqn
        if unfinished:
            self.logger.info(f"Finishing {len(unfinished)} table move(s) an earlier sync did not write")
            await self._move_renamed_tables(
                unfinished, blocked=set(), checkpoint=_already_saved, expected_ids=expected,
            )

    async def _remove_occupant(self, fqn: str, table: Record) -> None:
        """Remove what is stored under a name a renamed table moves into.

        A table that references the renamed one may have left a stand-in
        there; the tables that reference it keep their foreign keys, moved to
        the real record.
        """
        async with self.data_store_provider.transaction() as tx_store:
            occupant = await tx_store.get_record_by_external_id(self.connector_id, fqn, raise_on_error=True)
        if occupant is None or occupant.id == table.id:
            return
        await self.data_entities_processor.move_incoming_relations(occupant.id, table.id)
        await self._remove_records([occupant.id])

    async def _move_renamed_columns(self, table_fqn: str, renames: dict[str, str]) -> bool:
        """Move a table's column records to their columns' new names; returns whether they moved."""
        try:
            async with self.data_store_provider.transaction() as tx_store:
                stored = {
                    c.external_record_id: c for c in await tx_store.get_records_by_parent(
                        self.connector_id, table_fqn, RecordType.SQL_COLUMN.value,
                    )
                }
            updates = [
                {"id": column.id, "externalRecordId": column_fqn(table_fqn, new_name)}
                for old_name, new_name in renames.items()
                if (column := stored.get(column_fqn(table_fqn, old_name))) is not None and column.id
            ]
            if updates:
                async with self.data_store_provider.transaction() as tx_store:
                    await tx_store.batch_update_nodes(updates, CollectionNames.RECORDS.value)
            return True
        except Exception as e:
            self.sync_stats.errors += 1
            self.logger.error(f"Could not move the renamed columns of {table_fqn}: {e}")
            return False

    async def _on_table_renamed(self, table_record_id: str) -> None:
        """Tell the processor a table, or one of its columns, was renamed."""
        try:
            await self.data_entities_processor.on_sql_table_renamed(table_record_id)
        except Exception as e:
            self.sync_stats.errors += 1
            self.logger.warning(f"Could not handle the rename of table record {table_record_id}: {e}")

    async def _follow_renames(self, diff: CatalogDiff, moved: dict[str, str], moves: Moves) -> None:
        """Move renamed columns, tell the processor of renames, and note what they mean downstream."""
        for fqn, change in sorted(diff.changed.items()):
            previous = change.previous_fqn
            if previous and previous not in moved:
                continue
            record = moves[previous][0] if previous else None
            if change.column_renames and not await self._move_renamed_columns(fqn, change.column_renames):
                # Their records stay under the old names and are replaced as
                # drops and adds: the table is reported as restructured.
                self.sync_outcome.restructured_tables.add(fqn)
                continue
            if not (previous or change.column_renames or change.rename_pending):
                continue
            if record is None:
                try:
                    record = await self.data_entities_processor.get_record_by_external_id(
                        connector_id=self.connector_id, external_record_id=fqn, raise_on_error=True,
                    )
                except Exception as e:
                    # Not told of the rename: reported as restructured instead, and the next tables still follow.
                    self.sync_stats.errors += 1
                    self.logger.warning(f"Could not look up the record of renamed table {fqn}: {e}")
                    self.sync_outcome.restructured_tables.add(fqn)
                    continue
            if record is None or not record.id:
                continue
            await self._on_table_renamed(record.id)
            self.sync_outcome.renamed.add(record.id)
            if change.names_only:
                # Its column pairs are ranked by name, and nothing else changed.
                self.sync_outcome.relations_only.add(record.id)


async def _already_saved(moved: dict[str, str], record_ids: dict[str, str]) -> bool:
    return True


def restructured_fqns(diff: CatalogDiff, listed: Collection[str]) -> set[str]:
    """Tables to report as restructured: new (and listed), or changed in more than names."""
    restructured = {fqn for fqn in diff.added if fqn in listed}
    restructured |= {fqn for fqn, change in diff.changed.items() if change.structure and not change.names_only}
    return restructured
