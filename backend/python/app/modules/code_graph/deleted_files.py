"""What a deleted code file meant to the rest of its repo's graph.

A deleted record takes its blocks and every edge into them in the same
transaction, so by the time an edge build runs, nothing says which other files
called into it. Those files must be re-resolved: a call bound to the deleted
definition now binds to another one of the same name, or to nothing, and a call
that was ambiguous between the two now has one candidate. Without that, a
deleted file could only be served by a full rebuild of the repo, and a branch
switch deletes many.

So, just before the delete, the names the file defined are kept on a sync point
of their own, one per deletion so concurrent deletes never overwrite each other.
The next build treats them as changed names, which re-resolves exactly the files
that reference them, and removes the sync points it served.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple
from uuid import uuid4

from app.config.constants.arangodb import CollectionNames
from app.modules.code_graph.edge_build_trigger import is_code_record, record_scope
from app.modules.code_graph.module_refs import module_refs_of_path

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from logging import Logger

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = [
    "DeletedFiles",
    "SYNC_POINT_TYPE",
    "defined_names",
    "forget",
    "pending",
    "remember",
    "was_remembered",
]

SYNC_POINT_TYPE = "codeEdgeDeletedFile"
_KEY_SUFFIX = "code-deleted-file"


class DeletedFiles(NamedTuple):
    """Deletions a build has not served yet: their sync point keys and names."""

    keys: list[str]
    names: set[str]


async def defined_names(graph_provider: IGraphDBProvider, org_id: str, record_id: str) -> set[str]:
    """Every name a file's callers could have bound to: its definitions, and the
    ``module:`` references an importer uses for the file itself."""
    rows = await graph_provider.get_nodes_by_filters(
        collection=CollectionNames.BLOCKS.value,
        filters={"orgId": org_id, "recordId": record_id},
        return_fields=["name", "kind"],
        raise_on_error=True,
    ) or []
    names = {
        row["name"] for row in rows
        if isinstance(row.get("name"), str) and row["name"] and row.get("kind") != "file_summary"
    }
    info = await graph_provider.get_file_paths_for_records(
        org_id=org_id, record_ids=[record_id], raise_on_error=True,
    )
    file_info = info.get(record_id)
    if file_info is not None and file_info.file_path:
        names |= module_refs_of_path(file_info.file_path)
    return names


async def remember(graph_provider: IGraphDBProvider | None,
                   records: Iterable[Mapping[str, Any] | None], log: Logger) -> None:
    """Keep the defined names of each code file about to be deleted.

    Never raises: a deletion goes ahead without it, and the delete event then
    finds no sync point for the record and asks for a full build instead.
    """
    if graph_provider is None:
        return
    for record in records:
        if not is_code_record(record):
            continue
        record_id = record.get("_key") or record.get("id")
        scope = record_scope(record)
        if scope is None or not (isinstance(record_id, str) and record_id):
            continue
        org_id, connector_id, record_group_id = scope
        try:
            names = await defined_names(graph_provider, org_id, record_id)
            await graph_provider.upsert_sync_point(
                sync_point_key=f"{record_group_id}/{_KEY_SUFFIX}/{record_id}/{uuid4().hex}",
                sync_point_data={
                    "orgId": org_id,
                    "connectorId": connector_id,
                    "recordGroupId": record_group_id,
                    "recordId": record_id,
                    "syncDataPointType": SYNC_POINT_TYPE,
                    "deletedNames": sorted(names),
                },
                collection=CollectionNames.SYNC_POINTS.value,
            )
        except Exception:
            log.warning("Could not keep the names of deleted code file %s; its delete "
                        "will ask for a full code edge build", record_id, exc_info=True)


async def was_remembered(graph_provider: IGraphDBProvider, org_id: str, record_id: str) -> bool:
    rows = await graph_provider.get_nodes_by_filters(
        collection=CollectionNames.SYNC_POINTS.value,
        filters={"orgId": org_id, "recordId": record_id, "syncDataPointType": SYNC_POINT_TYPE},
        return_fields=["syncPointKey"],
        raise_on_error=True,
    )
    return bool(rows)


async def pending(graph_provider: IGraphDBProvider, org_id: str, record_group_id: str) -> DeletedFiles:
    """The deletions a build may serve: those whose record is gone.

    The names are kept before the delete commits. A build that read them in
    between would re-resolve the callers while the file's blocks were still
    there, then remove the sync point, and nothing would re-resolve them after
    the delete. A record in the trash keeps its blocks, so it waits too.
    """
    rows = await graph_provider.get_nodes_by_filters(
        collection=CollectionNames.SYNC_POINTS.value,
        filters={"orgId": org_id, "recordGroupId": record_group_id,
                 "syncDataPointType": SYNC_POINT_TYPE},
        return_fields=["syncPointKey", "recordId", "deletedNames"],
        # Unreadable must not read as "nothing deleted": the build would leave
        # the deleted files' callers bound as they were.
        raise_on_error=True,
    ) or []
    record_ids = sorted({r["recordId"] for r in rows if isinstance(r.get("recordId"), str)})
    present = {
        row.get("_key") for row in await graph_provider.get_nodes_by_field_in(
            CollectionNames.RECORDS.value, "_key", record_ids, return_fields=["_key"],
            raise_on_error=True,
        )
    } if record_ids else set()
    keys: list[str] = []
    names: set[str] = set()
    for row in rows:
        key = row.get("syncPointKey")
        if isinstance(key, str) and key and row.get("recordId") not in present:
            keys.append(key)
            names.update(n for n in row.get("deletedNames") or () if isinstance(n, str) and n)
    return DeletedFiles(keys, names)


async def forget(graph_provider: IGraphDBProvider, keys: Iterable[str], log: Logger) -> None:
    """Remove the sync points a settled build served; one written since stays.

    Never raises: the build already settled, and a sync point left behind only
    makes a later build re-resolve the same callers again.
    """
    for key in keys:
        try:
            await graph_provider.remove_sync_point(key, CollectionNames.SYNC_POINTS.value)
        except Exception:
            log.warning("Could not remove deleted code file sync point %s; a later build "
                        "serves it again", key, exc_info=True)
