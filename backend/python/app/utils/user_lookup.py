"""Reading the caller's user and org records, and the records a route serves, so that an outage is not reported as a miss."""

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
from app.utils import error_class
from app.utils.jwt import mint_service_token
from app.utils.logger import create_logger
from app.utils.user_messages import DATABASE_UNREACHABLE

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.config.configuration_service import ConfigurationService
    from app.models.entities import Record
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
    graph_provider: IGraphDBProvider, user_id: str | None, *, raise_on_error: bool = False, org_id: str | None = None
) -> dict[str, Any] | None:
    """The user's graph record, read once more after the caller's own record is written when it is missing.

    A brand-new account calls before the userAdded event that writes its graph user has been consumed. Only the user
    the request's verified token names, in that token's org, is written, from their live identity record; anyone
    else, a service or job, or an account that is deleted or disabled, stays None.

    ``org_id``, when the caller knows its organisation, binds both reads to it.
    """
    # Passed only when asked for, so each call site keeps the call it made before.
    read = {"raise_on_error": True} if raise_on_error else {}
    if org_id is not None:
        read.update(org_id=org_id)
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


async def get_user_or_503(
    graph_provider: IGraphDBProvider, user_id: str | None, *, org_id: str | None = None,
) -> dict[str, Any] | None:
    """The user's record, or None when there is no such user. ``org_id``, when given, binds the read to that org.

    Without ``raise_on_error`` the providers answer a failed read with None too,
    and a route that turns None into 404 or 401 tells a person who is there that
    they are not; a 401 also signs them out. A failed read is a 503 instead.
    """
    try:
        if org_id is None:
            return await get_caller_user(graph_provider, user_id, raise_on_error=True)
        return await get_caller_user(graph_provider, user_id, raise_on_error=True, org_id=org_id)
    except Exception as e:
        raise _database_unreachable(e) from e


async def get_org_or_503(graph_provider: IGraphDBProvider, org_id: str) -> dict[str, Any] | None:
    """The org's record, or None when there is none; a failed read is a 503."""
    return await get_document_or_503(graph_provider, org_id, CollectionNames.ORGS.value)


async def get_document_or_503(graph_provider: IGraphDBProvider, key: str, collection: str) -> dict[str, Any] | None:
    """The document, or None when there is none; a failed read is a 503."""
    try:
        return await graph_provider.get_document(key, collection, raise_on_error=True)
    except Exception as e:
        raise _database_unreachable(e) from e


async def get_record_or_503(graph_provider: IGraphDBProvider, record_id: str) -> Record | None:
    """The typed record, or None when there is none or it will not build; a failed read is a 503."""
    try:
        return await graph_provider.get_record_by_id(record_id, raise_on_error=True)
    except Exception as e:
        raise _database_unreachable(e) from e


async def check_record_access_or_503(
    graph_provider: IGraphDBProvider, user_id: str, org_id: str, record_id: str
) -> dict[str, Any] | None:
    """The record's access details, or None when the user may not open it; a failed read is a 503.

    Like its siblings it answers every exception with 503, a bug of ours included; the cause is kept, text-hidden, for the
    request log.
    """
    try:
        return await graph_provider.check_record_access_with_details(user_id, org_id, record_id)
    except Exception as e:
        raise _database_unreachable(e) from e


def raise_if_unavailable(cause: BaseException) -> None:
    """Raise the 503 (with Retry-After) for a failure the store could not answer just now (``error_class.retry_later``), so
    the web app asks again by itself; any other failure returns, for the caller's own 500."""
    if error_class.retry_later(cause):
        raise _database_unreachable(cause) from cause


async def await_or_503[T](op: Awaitable[T]) -> T:
    """``op``'s result; a failure the store could not answer just now is :func:`raise_if_unavailable`'s 503, any
    other is raised as is, for the route's own 500."""
    try:
        return await op
    except Exception as e:
        raise_if_unavailable(e)
        raise
async def read_or_503[T](read: Awaitable[T]) -> T:
    """The answer of a graph read none of the helpers above makes (an access check that also weighs chat content,
    say), with a failed read a 503 as theirs is."""
    try:
        return await read
    except Exception as e:
        raise _database_unreachable(e) from e


def _database_unreachable(cause: BaseException) -> HTTPException:
    error_class.mark_error(cause, hide_text=True)  # a driver's text can quote what was sent (an email, a value)
    return HTTPException(
        status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value,
        detail=DATABASE_UNREACHABLE,
        headers={"Retry-After": "5"},
    )


def is_live_identity(node: dict[str, Any]) -> bool:
    """False once an account has been removed or switched off.

    Two separate signals, because they are written by different things.
    Soft-delete leaves the user node and clears `isActive`
    (`entity.py` `_handle_user_deleted`). Disabling is its own field, carried
    from user management on the update event, and deliberately not stored in
    `isActive` — a rename sets that one back to true, so a disable kept there
    would not survive one.

    Absence is read as live for both. Nodes written before these fields
    existed carry neither, and treating those as dead would cut off agents
    that work today.
    """
    return (
        node.get("isActive", True) is not False
        and node.get("isDisabled", False) is not True
    )
