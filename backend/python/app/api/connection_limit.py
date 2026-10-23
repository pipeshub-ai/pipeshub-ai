"""Keeps a connected app (an OAuth app or a personal access token) to what its
user let it see, on the routes that reach a node by id or list nodes."""

from collections.abc import Awaitable, Iterable, Sequence
from typing import Any, TypeVar

from fastapi import HTTPException, Request

from app.api.middlewares.auth import connection_limit
from app.config.constants.arangodb import CollectionNames
from app.config.constants.http_status_code import HttpStatusCode
from app.modules.retrieval.selection_scope import (
    CONNECTION_LIMIT_TOO_LARGE_MESSAGE,
    CONNECTION_OUTSIDE_MESSAGE,
    NamedNode,
    SelectionTooLargeError,
    connection_coverage,
    connection_named_nodes,
    names_below_apps,
    outside_connection,
)
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

_T = TypeVar("_T")


async def _resolved(work: Awaitable[_T]) -> _T:
    """``work``, with a limit too large to resolve answered as a 422."""
    try:
        return await work
    except SelectionTooLargeError as exc:
        raise HTTPException(
            status_code=HttpStatusCode.UNPROCESSABLE_ENTITY.value, detail=CONNECTION_LIMIT_TOO_LARGE_MESSAGE,
        ) from exc


async def ids_outside_connection(
    request: Request, graph_provider: IGraphDBProvider, org_id: str, user_id: str, node_ids: Iterable[str],
) -> set[str]:
    """Of ``node_ids``, those the calling app may not see; none without a limit."""
    return await _resolved(outside_connection(
        graph_provider, user_id, org_id, node_ids, connection_limit(request.state.user),
    ))


async def connection_apps(
    request: Request, graph_provider: IGraphDBProvider, org_id: str, user_id: str,
) -> tuple[frozenset[str], frozenset[str]]:
    """The apps and collections the calling app's limit names whole, and those
    it reaches only through the folders and files it names. Call it only for a
    request that carries a limit."""
    return await _resolved(connection_coverage(
        graph_provider, user_id, org_id, connection_limit(request.state.user) or {},
    ))


async def named_inside_connection(
    request: Request, graph_provider: IGraphDBProvider, org_id: str, user_id: str,
) -> list[NamedNode]:
    """What the calling app's limit names that its user can read: the items a
    browse of its root lists. Call it only for a request that carries a limit."""
    return await _resolved(connection_named_nodes(
        graph_provider, user_id, org_id, connection_limit(request.state.user) or {},
    ))


def root_lists_what_the_limit_names(limit: dict[str, list[str]] | None) -> bool:
    """Whether a browse of the root lists the items a limit names: any limit
    that names a folder or file. A limit beside whole apps lists those apps as
    items too, as the chat agent's list_files does."""
    return names_below_apps(limit)


async def ids_outside_connection_on_restore(
    request: Request,
    graph_provider: IGraphDBProvider,
    org_id: str,
    user_id: str,
    kb_id: str,
    live_ids: Sequence[str],
    trashed: Sequence[dict[str, Any]],
) -> set[str]:
    """Of a restore's ids, those the calling app may not reach.

    ``check_access`` never returns a deleted node, so a member of a delete
    batch (``trashed``, as ``get_records_in_delete_batch`` returns it) is judged
    by where it sits: a limit that names it or a folder above it admits it,
    else the live folder its batch root came from, else the collection.
    """
    limit = connection_limit(request.state.user)
    if limit is None:
        return set()
    by_id = {item["record"]["_key"]: item for item in trashed}
    named = {*(limit.get("recordGroups") or ()), *(limit.get("records") or ())}
    anchors = {item.get("parentId") or kb_id for item in trashed if item.get("parentId") not in by_id}
    outside = await ids_outside_connection(request, graph_provider, org_id, user_id, [*live_ids, *anchors])

    def inside(key: str) -> bool:
        for _ in range(len(by_id)):
            parent = by_id[key].get("parentId")
            if key in named:
                return True
            if parent not in by_id:
                return (parent or kb_id) not in outside
            key = parent
        return False

    return {i for i in live_ids if i in outside} | {key for key in by_id if not inside(key)}


async def refuse_outside_connection(
    request: Request, graph_provider: IGraphDBProvider, org_id: str, user_id: str, node_id: str,
) -> None:
    """403 when the calling app may not see ``node_id``."""
    if await ids_outside_connection(request, graph_provider, org_id, user_id, [node_id]):
        raise HTTPException(status_code=HttpStatusCode.FORBIDDEN.value, detail=CONNECTION_OUTSIDE_MESSAGE)


#: Path parameters that name a knowledge base, record group, folder or record.
_INNER_PATH_PARAMS = ("folder_id", "parent_folder_id", "record_id")
_NODE_PATH_PARAMS = ("kb_id", "record_group_id", *_INNER_PATH_PARAMS)


async def keep_path_inside_connection(request: Request) -> None:
    """Router dependency: a connected app reaches no knowledge base, folder or
    record outside its limit by id, to read it or to change it."""
    # A restore names an item in the trash, which check_access never returns;
    # the restore itself checks everything it brings back.
    if str(getattr(request.scope.get("route"), "path", "")).endswith("/restore"):
        return
    ids = [str(request.path_params[key]) for key in _NODE_PATH_PARAMS if request.path_params.get(key)]
    if not ids:
        return
    user = request.state.user
    graph_provider = request.app.state.graph_provider
    outside = await ids_outside_connection(request, graph_provider, user.get("orgId"), user.get("userId"), ids)
    if outside and not await _only_the_collection_outside(request, graph_provider, outside):
        raise HTTPException(status_code=HttpStatusCode.FORBIDDEN.value, detail=CONNECTION_OUTSIDE_MESSAGE)


async def _only_the_collection_outside(
    request: Request, graph_provider: IGraphDBProvider, outside: set[str],
) -> bool:
    """A limit that names a folder admits it though not the collection above it,
    as long as the folder or record in the path lies in that collection."""
    kb_id = request.path_params.get("kb_id")
    inner = [str(request.path_params[key]) for key in _INNER_PATH_PARAMS if request.path_params.get(key)]
    if not kb_id or outside != {str(kb_id)} or not inner:
        return False
    for node_id in inner:
        node = await graph_provider.get_document(node_id, CollectionNames.RECORDS.value)
        if not node or node.get("connectorId") != str(kb_id):
            return False
    return True
