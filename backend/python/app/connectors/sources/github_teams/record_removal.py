"""
Removing records and record groups that are gone from GitHub or from the sync's scope.

Everything goes through the processor's delete path, which also tells the indexing
service to drop the record's vectors, or moves the record to the trash when that is
switched on. Callers decide *what* is gone; this module only removes it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.models.entities import Record, RecordType

if TYPE_CHECKING:
    from logging import Logger

    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )

_GROUP_PAGE = 500


class RecordRemover:
    """Deletes this connector's records and record groups."""

    def __init__(self, processor: DataSourceEntitiesProcessor, connector_id: str, logger: Logger) -> None:
        self._processor = processor
        self._connector_id = connector_id
        self._logger = logger

    async def remove_records(self, records: list[Record]) -> None:
        """Remove records with their attachments.

        Attachments hang off their issue or pull request by ATTACHMENT edges and go
        with it. Sub-issues are tickets in their own right: they stay, and lose the
        parent link that would point at a deleted record.
        """
        if not records:
            return
        survivors: list[str] = []
        for record in records:
            if not record.external_record_id:
                continue
            children = await self._processor.get_records_by_parent(
                self._connector_id, record.external_record_id
            )
            survivors.extend(c.id for c in children if c.record_type == RecordType.TICKET.value)
        removed_ids = {r.id for r in records}
        survivors = [s for s in dict.fromkeys(survivors) if s not in removed_ids]
        if survivors:
            await self._processor.on_records_detached_from_parent(survivors)
        result = await self._processor.on_records_deleted_cascade(
            [r.id for r in records], self._connector_id,
            cascade_children=False, include_trashed_roots=True,
        )
        self._raise_on_failure(result, len(records))

    async def remove_group(self, external_group_id: str) -> int:
        """Remove every record of a record group, then the group itself."""
        removed = 0
        previous: list[str] = []
        while True:
            page = await self._processor.get_records_in_record_group(
                self._connector_id, external_group_id, _GROUP_PAGE
            )
            ids = [r.id for r in page]
            if not ids:
                break
            if ids == previous:
                # The delete is not taking effect: stop instead of looping for ever.
                raise RuntimeError(f"Records of GitHub record group {external_group_id} could not be removed")
            result = await self._processor.on_records_deleted_cascade(
                ids, self._connector_id, cascade_children=False, include_trashed_roots=True,
            )
            self._raise_on_failure(result, len(ids))
            removed += len(ids)
            previous = ids
        await self._processor.on_record_group_deleted(external_group_id, self._connector_id)
        return removed

    @staticmethod
    def _raise_on_failure(result: dict | None, requested: int) -> None:
        result = result or {}
        if result.get("success") is False:
            raise RuntimeError(f"Could not remove {requested} GitHub record(s): {result.get('reason')}")
