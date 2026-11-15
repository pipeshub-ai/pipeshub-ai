"""Reading the caller's user and org records so that an outage is not reported as a miss."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

from app.config.constants.arangodb import CollectionNames
from app.config.constants.http_status_code import HttpStatusCode
from app.utils.user_messages import DATABASE_UNREACHABLE

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

# Set by the service that consumes userAdded (EntityEventService.ensure_caller_user): writes the request's own caller
# into the graph from their live identity record. Unset elsewhere, where a missing user stays missing.
_ensurer: dict[str, Callable[[str], Awaitable[bool]]] = {}


def register_caller_node_ensurer(ensure: Callable[[str], Awaitable[bool]]) -> None:
    _ensurer["caller_node"] = ensure


async def get_caller_user(
    graph_provider: IGraphDBProvider, user_id: str | None, *, raise_on_error: bool = False
) -> dict[str, Any] | None:
    """The user's graph record, read once more after the caller's own record is written when it is missing.

    A brand-new account calls before the userAdded event that writes its graph user has been consumed. The ensurer
    writes only the request's own caller, from their live identity record; anyone else, or an account that is
    deleted or disabled, stays None.
    """
    user = await graph_provider.get_user_by_user_id(user_id, raise_on_error=raise_on_error)
    ensure = _ensurer.get("caller_node")
    if user or not user_id or ensure is None or not await ensure(user_id):
        return user
    return await graph_provider.get_user_by_user_id(user_id, raise_on_error=raise_on_error)


async def get_user_or_503(graph_provider: IGraphDBProvider, user_id: str | None) -> dict[str, Any] | None:
    """The user's record, or None when there is no such user.

    Without ``raise_on_error`` the providers answer a failed read with None too,
    and a route that turns None into 404 or 401 tells a person who is there that
    they are not; a 401 also signs them out. A failed read is a 503 instead.
    """
    try:
        return await get_caller_user(graph_provider, user_id, raise_on_error=True)
    except Exception as e:
        raise _database_unreachable() from e


async def get_org_or_503(graph_provider: IGraphDBProvider, org_id: str) -> dict[str, Any] | None:
    """The org's record, or None when there is none; a failed read is a 503."""
    try:
        return await graph_provider.get_document(org_id, CollectionNames.ORGS.value, raise_on_error=True)
    except Exception as e:
        raise _database_unreachable() from e


def _database_unreachable() -> HTTPException:
    return HTTPException(
        status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value,
        detail=DATABASE_UNREACHABLE,
        headers={"Retry-After": "5"},
    )
