"""Reading the caller's user and org records so that an outage is not reported as a miss."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
from fastapi import HTTPException

from app.config.constants.arangodb import CollectionNames
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import (
    DefaultEndpoints,
    TokenScopes,
    config_node_constants,
)
from app.core import org_context
from app.utils.jwt import mint_service_token
from app.utils.logger import create_logger
from app.utils.user_messages import DATABASE_UNREACHABLE

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.config.configuration_service import ConfigurationService
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

_logger = create_logger(__name__)

# How a missing caller is written: in the connectors service, which consumes userAdded, its own
# EntityEventService.ensure_user; in the query service, a call to that service (connectors_user_node_ensurer). Unset
# elsewhere, where a missing user stays missing. Takes (org id, user id); answers whether the user was written.
_ensurer: dict[str, Callable[[str, str], Awaitable[bool]]] = {}

USER_NODE_ENSURE_PATH = "/api/v1/entity/internal/user-node"
_ENSURE_TIMEOUT_SECONDS = 30.0


def register_caller_node_ensurer(ensure: Callable[[str, str], Awaitable[bool]]) -> None:
    _ensurer["caller_node"] = ensure


def caller_node_ensurer() -> Callable[[str, str], Awaitable[bool]] | None:
    return _ensurer.get("caller_node")


async def get_caller_user(
    graph_provider: IGraphDBProvider, user_id: str | None, *, raise_on_error: bool = False
) -> dict[str, Any] | None:
    """The user's graph record, read once more after the caller's own record is written when it is missing.

    A brand-new account calls before the userAdded event that writes its graph user has been consumed. Only the user
    the request's verified token names, in that token's org, is written, from their live identity record; anyone
    else, a service or job, or an account that is deleted or disabled, stays None.
    """
    # Passed only when asked for, so each call site keeps the call it made before.
    read = {"raise_on_error": True} if raise_on_error else {}
    user = await graph_provider.get_user_by_user_id(user_id, **read)
    if user or not user_id:
        return user
    ctx = org_context.peek()
    ensure = _ensurer.get("caller_node")
    if ensure is None or ctx is None or ctx.kind != "user" or not ctx.org_id or ctx.user_id != user_id:
        return user
    # Read again whatever it answered: another writer of this user (the event, another worker) may have won the write.
    await ensure(ctx.org_id, user_id)
    return await graph_provider.get_user_by_user_id(user_id, **read)


def connectors_user_node_ensurer(config_service: ConfigurationService) -> Callable[[str, str], Awaitable[bool]]:
    """The query service's ensurer: asks the connectors service, which registers the user's private knowledge base
    connector along with the user, to write them. The service token names exactly that user and org."""

    async def ensure(org_id: str, user_id: str) -> bool:
        try:
            secret = ((await config_service.get_config(config_node_constants.SECRET_KEYS.value)) or {}).get(
                "scopedJwtSecret"
            )
            if not secret:
                raise RuntimeError("the scoped JWT secret is not configured")
            token = mint_service_token(
                secret, {"orgId": org_id, "userId": user_id, "scopes": [TokenScopes.USER_NODE_ENSURE.value]}
            )
            endpoints = (await config_service.get_config(config_node_constants.ENDPOINTS.value)) or {}
            base = (endpoints.get("connectors") or {}).get("endpoint") or DefaultEndpoints.CONNECTOR_ENDPOINT.value
            async with httpx.AsyncClient(timeout=_ENSURE_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    f"{base.rstrip('/')}{USER_NODE_ENSURE_PATH}", headers={"Authorization": f"Bearer {token}"}
                )
            response.raise_for_status()
            return bool(response.json().get("written"))
        except Exception as e:
            _logger.warning("Could not have the connectors service write user %s (%s)", user_id, type(e).__name__)
            return False

    return ensure


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
