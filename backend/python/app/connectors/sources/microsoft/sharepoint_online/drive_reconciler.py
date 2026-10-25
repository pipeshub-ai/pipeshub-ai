"""Removes the stored records of a document library that a complete listing of it no longer has.

A delta enumeration that starts without a token lists every item the library holds and no deletions. It
happens after Graph refuses a delta token (410 resyncRequired), after a stored token is lost, and on a
connector's first run. Whatever was deleted in between is never reported, so it would stay searchable.

Only a listing that ran to the end without an error is an answer to "what is gone", and even then each
record is removed only once the source answers 404 for that very item: an item a listing leaves out is a
candidate, not a deletion.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from logging import Logger

    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )

_PAGE = 500


class DriveReconciler:
    def __init__(self, processor: "DataSourceEntitiesProcessor", connector_id: str, logger: Logger) -> None:
        self.processor = processor
        self.connector_id = connector_id
        self.logger = logger

    async def remove_unlisted(
        self, drive_id: str, listed_external_ids: set[str], is_gone: Callable[[str], Awaitable[bool]]
    ) -> int:
        """Deletes the library's records that the complete listing did not contain and that ``is_gone``
        (given the record's external id) confirms the source no longer has."""
        removed = 0
        after_key = None
        while True:
            page = await self.processor.get_records_in_record_group(
                self.connector_id, drive_id, _PAGE, after_key=after_key
            )
            # Pages are keyed on the last record id, so deleting from this page does not shift the next one.
            for record in page:
                if record.external_record_id not in listed_external_ids and await is_gone(record.external_record_id):
                    await self.processor.on_record_deleted(record_id=record.id)
                    removed += 1
            if len(page) < _PAGE:
                break
            after_key = page[-1].id
        if removed:
            self.logger.info(
                f"Removed {removed} record(s) of library {drive_id} that its complete listing no longer has"
            )
        return removed
