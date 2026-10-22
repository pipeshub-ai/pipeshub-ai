"""Keeps a connected app (an OAuth app or a personal access token) to what its
user let it see, on the routes that reach a node by id or list nodes."""

from collections.abc import Iterable

from fastapi import HTTPException, Request

from app.api.middlewares.auth import connection_limit
from app.config.constants.http_status_code import HttpStatusCode
from app.modules.retrieval.selection_scope import (
    CONNECTION_LIMIT_TOO_LARGE_MESSAGE,
    CONNECTION_OUTSIDE_MESSAGE,
    SelectionTooLargeError,
    outside_connection,
)
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider


async def ids_outside_connection(
    request: Request, graph_provider: IGraphDBProvider, org_id: str, user_id: str, node_ids: Iterable[str],
) -> set[str]:
    """Of ``node_ids``, those the calling app may not see; none without a limit."""
    try:
        return await outside_connection(
            graph_provider, user_id, org_id, node_ids, connection_limit(request.state.user),
        )
    except SelectionTooLargeError as exc:
        raise HTTPException(
            status_code=HttpStatusCode.UNPROCESSABLE_ENTITY.value, detail=CONNECTION_LIMIT_TOO_LARGE_MESSAGE,
        ) from exc


async def refuse_outside_connection(
    request: Request, graph_provider: IGraphDBProvider, org_id: str, user_id: str, node_id: str,
) -> None:
    """403 when the calling app may not see ``node_id``."""
    if await ids_outside_connection(request, graph_provider, org_id, user_id, [node_id]):
        raise HTTPException(status_code=HttpStatusCode.FORBIDDEN.value, detail=CONNECTION_OUTSIDE_MESSAGE)


#: Path parameters that name a knowledge base, record group, folder or record.
_NODE_PATH_PARAMS = ("kb_id", "record_group_id", "folder_id", "parent_folder_id", "record_id")


async def keep_path_inside_connection(request: Request) -> None:
    """Router dependency: a connected app reaches no knowledge base, folder or
    record outside its limit by id, to read it or to change it."""
    ids = [str(request.path_params[key]) for key in _NODE_PATH_PARAMS if request.path_params.get(key)]
    if not ids:
        return
    user = request.state.user
    outside = await ids_outside_connection(
        request, request.app.state.graph_provider, user.get("orgId"), user.get("userId"), ids,
    )
    if outside:
        raise HTTPException(status_code=HttpStatusCode.FORBIDDEN.value, detail=CONNECTION_OUTSIDE_MESSAGE)
