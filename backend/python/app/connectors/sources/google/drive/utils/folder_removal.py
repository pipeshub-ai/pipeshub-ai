"""Removing a folder Drive reported `removed`, and what is stored under it.

One job: delete the folder's record and each stored record below it that the source confirms can no longer
be opened, and keep the rest.

Drive reports `removed` for a folder that was deleted, but also for one the user lost access to. In the first
case Drive may report only the folder, so deleting the folder alone left its files searchable. In the second,
an item inside can still be open through its own share, so deleting the whole tree lost it. Each stored item
under the folder is therefore asked about (`still_open`); only "not found", a lasting refusal or the trash
removes it. An item kept is re-pointed at the folder's parent by the processor's single-record delete.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from app.config.constants.arangodb import FOLDER_MIME_TYPES

if TYPE_CHECKING:
    from logging import Logger

    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )
    from app.models.entities import Record

StillOpen = Callable[["Record"], Awaitable[bool]]
MAX_ITEMS = 5000


async def remove_folder_confirming_contents(
    processor: "DataSourceEntitiesProcessor",
    connector_id: str,
    folder: "Record",
    still_open: StillOpen,
    logger: "Logger",
) -> None:
    """`still_open` raises on an answer that is not definite, so nothing is removed on a guess."""
    below: list[Record] = []
    frontier = [folder]
    seen = {folder.id}
    while frontier and len(below) < MAX_ITEMS:
        children: list[Record] = []
        for node in frontier:
            for child in await processor.get_records_by_parent(connector_id, node.external_record_id):
                if child.id not in seen:
                    seen.add(child.id)
                    children.append(child)
        below.extend(children)
        frontier = [c for c in children if c.mime_type in FOLDER_MIME_TYPES]
    kept = 0
    # Deepest first, so a folder removed after its contents leaves nothing to re-point.
    for record in reversed(below):
        if record.is_placeholder or await still_open(record):
            kept += 1
            continue
        await processor.on_record_deleted(record_id=record.id)
    await processor.on_record_deleted(record_id=folder.id)
    logger.info(
        "Removed folder %s and %d item(s) under it; kept %d still open elsewhere",
        folder.record_name, len(below) - kept, kept,
    )
