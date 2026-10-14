"""Reading a user's graph record so that an outage is not reported as a missing user."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

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
        raise HTTPException(
            status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value,
            detail=DATABASE_UNREACHABLE,
            headers={"Retry-After": "5"},
        ) from e
