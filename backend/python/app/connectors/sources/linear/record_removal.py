"""Removing records that are gone from Linear.

Everything goes through the processor's delete path, which also tells the indexing
service to drop the record's vectors (or moves it to the trash when that is switched on).
"""

from logging import Logger

from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.models.entities import Record, RecordType

GROUP_PAGE = 500


class RecordRemover:
    def __init__(self, processor: DataSourceEntitiesProcessor, connector_id: str, logger: Logger) -> None:
        self._processor = processor
        self._connector_id = connector_id
        self._logger = logger

    async def remove(self, external_record_id: str) -> bool:
        """Remove a record together with what only exists because of it.

        An issue's links, documents and files go with it. Its sub-issues are issues in
        their own right: they stay and lose their parent link.
        """
        record = await self._processor.get_record_by_external_id(self._connector_id, external_record_id)
        if record is None:
            return False
        await self.remove_record(record)
        return True

    async def remove_record(self, record: Record) -> None:
        dependents, survivors = await self._split_children(record)
        ids: list[str] = []
        for child in dependents:
            grandchildren, _ = await self._split_children(child)
            ids.extend(g.id for g in grandchildren)
            ids.append(child.id)
        ids.append(record.id)
        if survivors:
            await self._processor.on_records_detached_from_parent([s.id for s in survivors])
        await self._delete(list(dict.fromkeys(ids)), include_trashed_roots=True)

    async def _delete(self, ids: list[str], **options: bool) -> None:
        """The processor reports an ordinary failure in its result instead of raising."""
        result = await self._processor.on_records_deleted_cascade(
            ids, self._connector_id, cascade_children=False, **options)
        if isinstance(result, dict) and result.get("success") is False:
            raise RuntimeError(f"Could not remove {len(ids)} Linear record(s): {result.get('reason')}")

    async def _split_children(self, record: Record) -> tuple[list[Record], list[Record]]:
        if not record.external_record_id:
            return [], []
        children = await self._processor.get_records_by_parent(self._connector_id, record.external_record_id)
        dependents = [c for c in children if c.record_type != RecordType.TICKET]
        survivors = [c for c in children if c.record_type == RecordType.TICKET]
        return dependents, survivors

    async def remove_group(self, external_group_id: str) -> int:
        """Remove a team: its records, its record group and its user group."""
        removed = await self.remove_group_records(external_group_id)
        await self._processor.on_record_group_deleted(external_group_id, self._connector_id)
        await self._processor.on_user_group_deleted(external_group_id, self._connector_id)
        self._logger.info("Removed Linear team %s and %s record(s)", external_group_id, removed)
        return removed

    async def remove_group_records(self, external_group_id: str) -> int:
        """Remove the records filed under a team, leaving the team itself."""
        removed = 0
        previous: list[str] = []
        while True:
            records = await self._processor.get_records_in_record_group(
                self._connector_id, external_group_id, GROUP_PAGE)
            ids = [r.id for r in records]
            if not ids:
                break
            if ids == previous:
                # The delete is not taking effect: stop instead of spinning.
                raise RuntimeError(f"Records of Linear team {external_group_id} could not be removed")
            await self._delete(ids)
            removed += len(ids)
            previous = ids
            if len(ids) < GROUP_PAGE:
                break
        return removed
