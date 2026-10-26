"""Reading the caller's user and org records so that an outage is not reported as a miss."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

from app.config.constants.arangodb import CollectionNames
from app.config.constants.http_status_code import HttpStatusCode
from app.utils.user_messages import DATABASE_UNREACHABLE

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider


async def get_user_or_503(graph_provider: IGraphDBProvider, user_id: str | None) -> dict[str, Any] | None:
    """The user's record, or None when there is no such user.

    Without ``raise_on_error`` the providers answer a failed read with None too,
    and a route that turns None into 404 or 401 tells a person who is there that
    they are not; a 401 also signs them out. A failed read is a 503 instead.
    """
    try:
        return await graph_provider.get_user_by_user_id(user_id, raise_on_error=True)
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
