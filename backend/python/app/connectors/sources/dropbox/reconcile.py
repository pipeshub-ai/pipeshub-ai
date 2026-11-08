"""After a folder was listed again from the start, remove what that listing no longer has.

Dropbox resets a cursor (`ListFolderContinueError.reset`) when it can no longer say what changed since it. The
connector then lists the folder from the start; that listing shows what exists but not what was deleted while the
cursor was dead. Each record of the folder's record group that the complete new listing did not show is checked by
its id (it may have moved to another folder, where it is listed on its own) and removed only once Dropbox says it
is gone. Nothing is removed when the new listing did not finish.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.connectors.sources.dropbox.deletes import DeleteOutcome, _remove_gone

if TYPE_CHECKING:
    import logging
    from collections.abc import Awaitable, Callable

    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )
    from app.models.entities import Record

_PAGE = 500


async def remove_unlisted(
    processor: DataSourceEntitiesProcessor,
    connector_id: str,
    record_group_id: str,
    listed_external_ids: set[str],
    still_exists: Callable[[Record], Awaitable[bool | None]],
    logger: logging.Logger,
) -> DeleteOutcome:
    unlisted: list[Record] = []
    after: str | None = None
    while True:
        page = await processor.get_records_in_record_group(connector_id, record_group_id, _PAGE, after)
        unlisted.extend(r for r in page if r.external_record_id not in listed_external_ids)
        if len(page) < _PAGE:
            break
        after = page[-1].id
    if not unlisted:
        return DeleteOutcome()
    logger.info(f"{len(unlisted)} record(s) of {record_group_id} were not in its new listing; checking each")
    return await _remove_gone(processor, connector_id, unlisted, still_exists, logger, f"group {record_group_id}")
