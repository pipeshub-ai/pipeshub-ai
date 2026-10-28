"""Keep the stored paths under a folder in step when the folder moves or is renamed.

Deletes are found by path (Dropbox's DeletedMetadata has no id), so every stored path must be the item's current
one. Dropbox reports a moved folder at its new path, but does not promise to list each child again (Greg-DB,
Dropbox forum 427410: the delete and the add are not even guaranteed to arrive together). A child it does not
re-list would keep its old path, and its later delete would match nothing. So when a folder's path changes, the
paths of everything stored under it are rewritten to the new prefix.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.config.constants.arangodb import CollectionNames

if TYPE_CHECKING:
    import logging

    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )

_MAX_NODES = 100_000


async def repath_descendants(
    processor: DataSourceEntitiesProcessor,
    connector_id: str,
    folder_external_id: str,
    old_prefix: str,
    new_prefix: str,
    logger: logging.Logger,
) -> int:
    """Rewrite ``old_prefix/...`` to ``new_prefix/...`` on every stored descendant of a folder. Returns how many."""
    old_prefix, new_prefix = old_prefix.rstrip("/"), new_prefix.rstrip("/")
    if old_prefix == new_prefix:
        return 0
    updates: list[dict] = []
    todo, seen = [folder_external_id], {folder_external_id}
    while todo and len(seen) < _MAX_NODES:
        parent = todo.pop()
        for child in await processor.get_records_by_parent(connector_id, parent):
            if child.external_record_id in seen:
                continue
            seen.add(child.external_record_id)
            todo.append(child.external_record_id)
            stored = await processor.get_file_record_by_id(child.id)
            path = getattr(stored, "path", None) or ""
            if path.startswith(old_prefix + "/"):
                updates.append({"id": child.id, "path": new_prefix + path[len(old_prefix):]})
    if updates:
        await processor.batch_update_nodes(updates, CollectionNames.FILES.value)
        logger.info(f"Moved the stored paths of {len(updates)} item(s) from {old_prefix} to {new_prefix}")
    return len(updates)
