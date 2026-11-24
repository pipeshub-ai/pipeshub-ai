"""The commit a code repository's index reflects, kept on its code record group (``lastSyncedCommit``).

Each file carries the commit it was synced at (``codeFiles.commitSha``), but a result that names no file -- a
path not found, an empty search, the repo listing -- has only the repo's. The GitHub and GitLab connectors
write it whenever a sync leaves the repo's index at its branch head; the code-graph tools read it.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.config.constants.arangodb import CollectionNames

if TYPE_CHECKING:
    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )

LAST_SYNCED_COMMIT = "lastSyncedCommit"
_RECORD_GROUPS = CollectionNames.RECORD_GROUPS.value

logger = logging.getLogger(__name__)


async def record_synced_commit(
    processor: DataSourceEntitiesProcessor, connector_id: str, external_group_id: str, commit_sha: str,
) -> bool:
    """Stamp the code record group *external_group_id* with *commit_sha*; False when it was not written.

    A patch, never an upsert: only this field changes. The sync it follows stands either way -- the stamp is
    retried by the next sync, which writes it again.
    """
    try:
        group = await processor.get_record_group_by_external_id(connector_id, external_group_id)
        if group is None:
            logger.warning("No record group %s in connector %s to stamp with its synced commit",
                           external_group_id, connector_id)
            return False
        return await processor.batch_update_nodes(
            [{"id": group.id, LAST_SYNCED_COMMIT: commit_sha}], _RECORD_GROUPS,
        ) is not False
    except Exception:
        logger.warning("Could not stamp record group %s with commit %s", external_group_id, commit_sha,
                       exc_info=True)
        return False
