"""A pull or merge request -MODIFIES-> the code files it changed.

Written where the connector reads the PR's file list (the index/stream path, the only place it exists), not at
sync. Every PR save makes the processor delete and rewrite the PR's LINK_RELATION_TYPES edges, so MODIFIES stays
out of that list and is cleaned up here instead: this PR's MODIFIES edges are replaced as a whole on each write.
A changed file with no code-file record in the PR's own connector is skipped -- never a placeholder.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, NamedTuple

from app.config.constants.arangodb import CollectionNames, RecordRelations
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )
    from app.connectors.core.base.data_store.data_store import TransactionStore
    from app.models.blocks import BlockGroup, FileChangeMetadata
    from app.models.entities import Record

__all__ = ["Linked", "link_changed_files", "write_modifies"]

logger = logging.getLogger(__name__)


class Linked(NamedTuple):
    written: int    # MODIFIES edges written
    unmatched: int  # changed files with no code-file record in the PR's connector

_RECORDS = CollectionNames.RECORDS.value
_LINKS = CollectionNames.NODE_RELATIONS.value  # a link type: the providers store it on RECORD_LINK / recordLinks
_MODIFIES = RecordRelations.MODIFIES.value


async def write_modifies(
    tx_store: TransactionStore,
    pr: Record,
    changes: Sequence[FileChangeMetadata],
    code_external_id: Callable[[str], str],
) -> Linked:
    """Replace *pr*'s MODIFIES edges with one per changed file that is a code-file record of its connector.

    *code_external_id* maps a repo-relative path to that connector's code-file external id. Returns how many
    files were linked and how many matched no record (not indexed as code, or ids built another way).
    """
    by_external = {code_external_id(c.path): c for c in changes if c.path}
    # Looked up before the delete, and a failed lookup raises: without explicit transactions the delete commits on
    # its own, so a lookup that failed after it would leave the PR with no edges.
    rows = (await tx_store.get_nodes_by_field_in(
        _RECORDS, "externalRecordId", sorted(by_external), ["_key", "externalRecordId", "connectorId"],
        raise_on_error=True,
    ) or []) if by_external else []
    # Raises on failure: 0 also means "nothing to delete", and new links written beside the old ones would be wrong.
    await tx_store.delete_edges_by_relationship_types(
        from_id=pr.id, from_collection=_RECORDS, collection=_LINKS, relationship_types=[_MODIFIES],
        raise_on_error=True,
    )
    if not by_external:
        return Linked(0, 0)
    now = get_epoch_timestamp_in_ms()
    edges = [
        {
            "_from": f"{_RECORDS}/{pr.id}",
            "_to": f"{_RECORDS}/{key}",
            "relationshipType": _MODIFIES,
            "status": change.status,
            "additions": change.additions,
            "deletions": change.deletions,
            "previousPath": change.previous_path,
            "mergedAt": getattr(pr, "merged_at", None),
            "createdAtTimestamp": now,
            "updatedAtTimestamp": now,
        }
        for row in rows
        if row.get("connectorId") == pr.connector_id and (key := row.get("_key") or row.get("id"))
        and (change := by_external.get(row.get("externalRecordId")))
    ]
    if edges:
        await tx_store.batch_create_edges(edges, collection=_LINKS)
    return Linked(len(edges), len(by_external) - len(edges))


async def link_changed_files(
    processor: DataSourceEntitiesProcessor,
    pr: Record,
    groups: Iterable[BlockGroup],
    code_external_id: Callable[[str], str],
) -> Linked | None:
    """After a connector built *pr*'s blocks from its (successfully read) file list: link the files its
    PR_FILE_CHANGE *groups* describe. A failure is logged, never raised: the PR's content still indexes."""
    changes = [g.file_change_metadata for g in groups if g.file_change_metadata is not None]
    try:
        linked = await processor.on_pr_file_changes(pr, changes, code_external_id)
    except Exception:
        logger.warning("Could not link pull request %s to the files it changed", pr.id, exc_info=True)
        return None
    if linked.unmatched:
        # Expected for files that are not indexed as code; a PR where none match is the sign of ids built
        # another way than the code walk builds them.
        logger.info("Pull request %s: linked %d of %d changed files; %d matched no code-file record",
                    pr.id, linked.written, linked.written + linked.unmatched, linked.unmatched)
    return linked
