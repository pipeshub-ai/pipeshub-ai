"""Apply a Dropbox delete, which the API reports by path only.

``DeletedMetadata`` carries no id, so the records stored at that path are found
through the file path the sync wrote. Dropbox may report a deleted folder alone
or with its children, and also reports a move as a delete at the old path, or a
shared folder a member left as a delete in that member's listing. So a record is
removed only once its own id no longer resolves, and it goes with its subtree.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import logging
    from collections.abc import Awaitable, Callable

    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )
    from app.models.entities import Record
    from app.sources.client.dropbox.dropbox_ import DropboxResponse

# How the Dropbox client reports a lookup of an id that no longer exists.
_NOT_FOUND = "not_found"


def item_still_exists(response: DropboxResponse) -> bool | None:
    """Read a ``files_get_metadata(<id>)`` answer: True, False when Dropbox says not_found, None when unknown."""
    if response.success:
        # The SDK returns DeletedMetadata (no id) only with include_deleted, which is not asked for.
        return getattr(response.data, "id", None) is not None
    return False if _NOT_FOUND in (response.error or "") else None


async def apply_deleted_entry(
    processor: DataSourceEntitiesProcessor,
    connector_id: str,
    path_lower: str,
    still_exists: Callable[[Record], Awaitable[bool | None]],
    logger: logging.Logger,
) -> bool:
    """Remove the records at ``path_lower`` whose item is gone at the source, with everything below them.

    Returns False when a record was kept for want of an answer or its delete failed.
    """
    done = True
    gone: list[str] = []
    for record in await processor.get_records_by_file_path(connector_id, path_lower):
        exists = await still_exists(record)
        if exists is None:
            logger.warning(f"Could not confirm Dropbox deleted {path_lower} ({record.external_record_id}); keeping it")
            done = False
        elif exists:
            logger.debug(f"{path_lower} still exists as {record.external_record_id} (moved or still shared); keeping it")
        else:
            gone.append(record.id)
    if gone:
        result = await processor.on_records_deleted_cascade(gone, connector_id, include_trashed_roots=True)
        if not (result or {}).get("success", False):
            logger.warning(f"Failed to remove the records deleted at {path_lower}: {(result or {}).get('reason')}")
            done = False
    return done
