"""Per-user access: a user connects their own source account to a team connector (connectors service).

In per-user mode a team connector shows each user only what the source returns to that user's own
credential (app.connectors.core.base.user_access). These routes let any member of the connector's
organisation connect, see and remove their own connection, and let an admin see and remove anyone's.
They never return a credential. A connector that is missing, of another organisation, or not in
per-user mode answers with the same 404.

A user connects either by pasting a token or by signing in with the source (OAuth) through the
connector's own OAuth app. The sign-in returns to the same callback page as the connector's own
OAuth; its ``state`` carries a marker that sends the callback here (``complete_user_sign_in``).

An admin sees everyone the source lists next to their PipesHub account and connection (the roster)
and can ask them to connect: an in-app notification and an email with a connect link. The link's
routes (``/user-access/link/*``) need no PipesHub sign-in: the link names the user, and the source
sign-in must still be an account with that user's email.
"""

from __future__ import annotations

import base64
import hmac
import json
import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app.api.middlewares.auth import is_request_admin, require_scopes
from app.config.configuration_service import delete_config_confirmed
from app.config.constants.arangodb import CollectionNames
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import OAuthScopes
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.core.base.token_service.oauth_service import OAuthProvider
from app.connectors.core.base.user_access import (
    METHOD_OAUTH,
    METHOD_TOKEN,
    STATUS_ACTIVE,
    ConnectLink,
    Invite,
    UserConnection,
    delete_connection,
    delete_invite,
    delete_user_links,
    get_connection,
    get_invite,
    get_members,
    is_per_user,
    list_connections,
    list_invites,
    resolve_link,
    save_connection,
    save_invite,
    sign_in_path,
)
from app.connectors.core.base.user_access_invites import (
    ConnectRequest,
    send_connect_request,
)
from app.connectors.core.registry.connector_builder import ConnectorScope
from app.connectors.core.sync.sync_dispatcher import SyncSpec, get_dispatcher
from app.connectors.sources.notion.per_user import (
    CONNECTOR_TYPE as NOTION_CONNECTOR_TYPE,
)
from app.connectors.sources.notion.per_user import (
    NotionAccessError,
    has_oauth_app,
    refresh_lock,
    user_oauth_config,
    verify_token,
)
from app.core.org_context import OrgContext, bind, current_org_id, spawn_detached
from app.services.cache.invalidation_hooks import notify_connector_permissions_changed
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider  # noqa: TC001 - FastAPI reads the annotation at runtime
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from app.utils.user_lookup import get_org_or_503, get_user_or_503

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Iterator

logger = logging.getLogger(__name__)

user_access_router = APIRouter(tags=["Connectors"])

# Connector types that support per-user mode.
_SUPPORTED_TYPES = frozenset({NOTION_CONNECTOR_TYPE})
_MAX_TOKEN_LENGTH = 2000
# Marks the OAuth state of a user's own sign-in, so the shared callback hands it to this module.
_SIGN_IN_STATE_KEY = "userAccess"
# In the state of a sign-in started from a connect link: the user the link names, and their organisation.
_LINK_USER_STATE_KEY = "linkUser"
_LINK_ORG_STATE_KEY = "linkOrg"
_MAX_LINK_TOKEN_LENGTH = 300
_MAX_INVITES = 500
_LINK_GONE = "This link has expired or was already used."
_SOURCE_NAMES = {NOTION_CONNECTOR_TYPE: "Notion"}
# Roster order: who needs attention first.
_STATUS_ORDER = ("needs_reconnect", "not_invited", "invited", "opted_out", "connected", "no_account")
_BACKGROUND: set[asyncio.Task[None]] = set()


async def get_graph_provider(request: Request) -> IGraphDBProvider:
    return request.app.state.graph_provider


def _not_found() -> HTTPException:
    return HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="Connector not found")


def _config_service(request: Request) -> Any:  # noqa: ANN401 - the container's provider is untyped
    return request.app.container.config_service()


async def _per_user_connector(request: Request, connector_id: str, graph_provider: IGraphDBProvider) -> dict[str, Any]:
    """The connector, when it is a team connector of the caller's organisation that runs in per-user mode.
    Its config is under ``"config"``."""
    org_id = current_org_id()
    app = await graph_provider.get_document(connector_id, CollectionNames.APPS.value)
    if (
        not app or not org_id
        or app.get("type") not in _SUPPORTED_TYPES
        or app.get("scope") != ConnectorScope.TEAM.value
        or not await request.app.state.connector_registry.belongs_to_org(app, org_id)
    ):
        raise _not_found()
    config = await _config_service(request).get_config(
        f"/services/connectors/{connector_id}/config", raise_on_error=True
    )
    if not is_per_user(config if isinstance(config, dict) else None):
        raise _not_found()
    return {**app, "config": config}


async def _caller(request: Request, graph_provider: IGraphDBProvider) -> dict[str, Any]:
    user = await get_user_or_503(graph_provider, request.state.user.get("userId"))
    if not user or not (user.get("id") or user.get("_key")) or not user.get("email"):
        raise HTTPException(status_code=HttpStatusCode.UNAUTHORIZED.value, detail="Unknown user")
    return user


def _is_member(user: dict[str, Any] | None, org_id: str) -> bool:
    """An active user of ``org_id`` with an email."""
    return bool(
        user and user.get("email") and str(user.get("orgId") or "") == org_id and user.get("isActive") is not False
    )


async def _withdraw(graph_provider: IGraphDBProvider, connector_id: str, user_key: str) -> None:
    """Take away the user's grants on the connector and their link to it, now rather than at the next sync."""
    try:
        # Through the store, which moves the connector's structure generation with a grant change.
        async with GraphDataStore(logger, graph_provider).transaction() as tx_store:
            await tx_store.remove_user_access_to_connector(connector_id, user_key)
            await tx_store.delete_edge(
                user_key, CollectionNames.USERS.value, connector_id, CollectionNames.APPS.value,
                CollectionNames.USER_APP_RELATION.value,
            )
    finally:
        await notify_connector_permissions_changed(connector_id, current_org_id())


async def _request_sync(app: dict[str, Any], connector_id: str) -> None:
    """Ask for a sync so a new connection is read soon. A busy or failed request waits for the schedule."""
    dispatcher = get_dispatcher()
    org_id = current_org_id()
    if dispatcher is None or not org_id:
        return
    try:
        await dispatcher.submit(SyncSpec(connector_id=connector_id, connector_name=str(app.get("type")), org_id=org_id))
    except Exception:
        logger.warning("Could not request a sync of %s after a user connected", connector_id, exc_info=True)


def _status(connection: UserConnection | None, config: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "connected": connection is not None,
        "connection": connection.public() if connection else None,
        "signInAvailable": has_oauth_app(config),
    }


@user_access_router.get(
    "/api/v1/connectors/user-access/mine",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))],
)
async def list_my_per_user_connectors(
    request: Request, graph_provider: IGraphDBProvider = Depends(get_graph_provider)
) -> dict[str, Any]:
    """The organisation's per-user connectors, each with whether the caller has connected. Only names and ids:
    any member may see that the connector exists, since connecting to it is how they get its content."""
    org_id = current_org_id()
    user = await _caller(request, graph_provider)
    user_id = str(user.get("userId"))
    if not org_id:
        return {"connectors": []}
    config_service = _config_service(request)
    out: list[dict[str, Any]] = []
    for connector_type in sorted(_SUPPORTED_TYPES):
        documents, _ = await graph_provider.get_filtered_connector_instances(
            collection=CollectionNames.APPS.value, edge_collection=CollectionNames.ORG_APP_RELATION.value,
            org_id=org_id, user_id=user_id, scope=ConnectorScope.TEAM.value, limit=200,
            is_admin=True, is_active=True, connector_type_filter=connector_type,
        )
        for app in documents:
            connector_id = str(app.get("_key") or app.get("id") or "")
            if not connector_id or app.get("type") != connector_type:
                continue
            config = await config_service.get_config(f"/services/connectors/{connector_id}/config", raise_on_error=True)
            if not is_per_user(config if isinstance(config, dict) else None):
                continue
            status = _status(await get_connection(config_service, connector_id, user_id), config)
            invite = await get_invite(config_service, connector_id, user_id)
            out.append({
                "connectorId": connector_id, "name": app.get("name"), "type": connector_type, **status,
                "invited": invite is not None and invite.send_count > 0,
                "optedOut": invite is not None and invite.opted_out,
            })
    return {"connectors": out}


@user_access_router.get(
    "/api/v1/connectors/{connector_id}/user-access",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))],
)
async def get_my_connection(
    connector_id: str, request: Request, graph_provider: IGraphDBProvider = Depends(get_graph_provider)
) -> dict[str, Any]:
    app = await _per_user_connector(request, connector_id, graph_provider)
    user = await _caller(request, graph_provider)
    return _status(await get_connection(_config_service(request), connector_id, str(user.get("userId"))), app["config"])


@user_access_router.post(
    "/api/v1/connectors/{connector_id}/user-access",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))],
)
async def connect_my_account(
    connector_id: str,
    request: Request,
    token: str = Body(..., embed=True, min_length=1, max_length=_MAX_TOKEN_LENGTH),
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
) -> dict[str, Any]:
    app = await _per_user_connector(request, connector_id, graph_provider)
    user = await _caller(request, graph_provider)
    connection = await _connect(request, app, connector_id, user, token.strip(), method=METHOD_TOKEN)
    return _status(connection, app["config"])


async def _connect(
    request: Request,
    app: dict[str, Any],
    connector_id: str,
    user: dict[str, Any],
    token: str,
    *,
    method: str,
    refresh_token: str | None = None,
) -> UserConnection:
    """Check who ``token`` belongs to and store it as the caller's connection. 400 for a credential
    that must be refused, 409 for a Notion account another user connected, 503 when Notion is down."""
    user_id, email = str(user.get("userId")), str(user["email"])
    config_service = _config_service(request)
    # Under the refresh lock: a disconnect that lands while the token is checked waits, then removes this.
    async with refresh_lock(connector_id, user_id):
        try:
            identity = await verify_token(
                token, pipeshub_email=email, connector_id=connector_id, config_service=config_service, logger=logger,
            )
        except NotionAccessError as e:
            raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail=str(e)) from None
        except Exception as e:
            logger.warning("Could not verify a Notion token for connector %s: %s", connector_id, e)
            raise HTTPException(
                status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value, detail="Notion could not be reached. Try again."
            ) from None

        for other in await list_connections(config_service, connector_id):
            if other.user_id != user_id and other.source_user_id == identity.user_id:
                raise HTTPException(
                    status_code=HttpStatusCode.CONFLICT.value,
                    detail="Another PipesHub user has already connected this Notion account.",
                )

        connection = UserConnection(
            user_id=user_id, email=email, token=token, source_user_id=identity.user_id,
            source_email=identity.email or "", workspace_id=identity.workspace_id,
            refresh_token=refresh_token, method=method,
        )
        await save_connection(config_service, connector_id, connection)
    await _drop_links(config_service, connector_id, user_id)
    try:
        await delete_invite(config_service, connector_id, user_id)
    except Exception as e:
        logger.warning("Could not close the invite of a user of connector %s: %s", connector_id, e)
    await _request_sync(app, connector_id)
    return connection


async def _drop_links(config_service: Any, connector_id: str, user_id: str) -> None:  # noqa: ANN401
    """The user's connect links stop working once they are connected or removed. Best effort: a
    link left behind still expires, and only ever connects its own user."""
    try:
        await delete_user_links(config_service, connector_id, user_id)
    except Exception as e:
        logger.warning("Could not delete the connect links of a user of connector %s: %s", connector_id, e)


@user_access_router.get(
    "/api/v1/connectors/{connector_id}/user-access/oauth/authorize",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))],
)
async def start_my_sign_in(
    connector_id: str, request: Request, graph_provider: IGraphDBProvider = Depends(get_graph_provider)
) -> dict[str, Any]:
    """The URL that signs the caller in with Notion through the connector's OAuth app. The sign-in
    returns to the connector OAuth callback page, which completes it (``complete_user_sign_in``)."""
    await _per_user_connector(request, connector_id, graph_provider)
    user = await _caller(request, graph_provider)
    return {"authorizationUrl": await _authorization_url(request, connector_id, str(user.get("userId")))}


async def _authorization_url(request: Request, connector_id: str, user_id: str, *, link_user: bool = False) -> str:
    """Where to send ``user_id`` to sign in with Notion. Its state is kept for that user only."""
    config_service = _config_service(request)
    try:
        oauth_config = await user_oauth_config(
            connector_id, config_service=config_service, org_id=str(current_org_id()), logger=logger,
            container=request.app.container,
        )
    except NotionAccessError as e:
        raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail=str(e)) from None
    provider = OAuthProvider(oauth_config, config_service, sign_in_path(connector_id, user_id))
    try:
        # owner=user: the token acts for this person, on the pages they pick (a Notion public connection).
        url = await provider.start_authorization(owner="user")
    finally:
        await provider.close()
    parts = urlparse(url)
    query = parse_qs(parts.query)
    query["state"] = [_encode_sign_in_state(query["state"][0], connector_id, user_id if link_user else None)]
    return urlunparse(parts._replace(query=urlencode(query, doseq=True)))


def _encode_sign_in_state(state: str, connector_id: str, link_user: str | None = None) -> str:
    """The connector OAuth state shape (``state``, ``connector_id``) plus the sign-in marker, and for
    a sign-in from a connect link, whose it is. Naming a user grants nothing: the callback checks the
    random ``state`` against the one stored for that user."""
    data: dict[str, Any] = {"state": state, "connector_id": connector_id, _SIGN_IN_STATE_KEY: True}
    if link_user:
        data[_LINK_USER_STATE_KEY] = link_user
        data[_LINK_ORG_STATE_KEY] = current_org_id()
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode()


def is_user_sign_in_state(state_data: object) -> bool:
    return isinstance(state_data, dict) and state_data.get(_SIGN_IN_STATE_KEY) is True


class _SignInFailed(Exception):
    """A user's sign-in that cannot complete; the message is shown to them. ``shareable`` marks a message
    about the Notion account they signed in with, safe to show on the public connect-link page too."""

    def __init__(self, message: str, *, shareable: bool = False) -> None:
        super().__init__(message)
        # Always one of this module's own messages (or a NotionAccessError's, written for the user), never
        # an internal error's text.
        self.user_message = message
        self.shareable = shareable


async def complete_user_sign_in(
    request: Request,
    graph_provider: IGraphDBProvider,
    *,
    connector_id: str,
    state: str,
    code: str,
    base_url: str,
    settings_url: str,
) -> dict[str, Any]:
    """The connector OAuth callback for a user's own sign-in: exchange the code with the state
    stored for the caller (so only the person who started it can finish it) and connect the token.

    Answers in the callback's shape and never raises: the callback marks the connector itself
    signed out on an error, and one user's failed sign-in must not do that to everyone's connector."""
    try:
        user = await _caller(request, graph_provider)
        await _complete_user_sign_in(request, graph_provider, user, connector_id=connector_id, state=state, code=code)
    except Exception as e:
        if not isinstance(e, _SignInFailed):
            logger.warning("Notion sign-in for connector %s failed: %s", connector_id, e)
        message = e.user_message if isinstance(e, _SignInFailed) else "Notion sign-in did not complete. Try again."
        return {
            "success": False, "error": "user_sign_in_failed", "error_message": message,
            "redirect_url": f"{base_url}/connectors/oauth/callback?oauth_error=user_sign_in_failed",
        }
    return {"success": True, "redirect_url": f"{settings_url}/{connector_id}"}


async def _complete_user_sign_in(
    request: Request, graph_provider: IGraphDBProvider, user: dict[str, Any], *,
    connector_id: str, state: str, code: str,
) -> dict[str, Any]:
    """Finish ``user``'s sign-in and connect them. Returns the connector."""
    try:
        app = await _per_user_connector(request, connector_id, graph_provider)
    except HTTPException:
        raise _SignInFailed("This connector does not let users connect their own Notion account.") from None
    config_service = _config_service(request)
    session = sign_in_path(connector_id, str(user.get("userId")))
    stored = await config_service.get_config(session, use_cache=False, keep_in_cache=False, raise_on_error=True)
    stored_state = str(((stored or {}).get("oauth") or {}).get("state") or "") if isinstance(stored, dict) else ""
    if not stored_state or not hmac.compare_digest(stored_state, state):
        # Not this user's sign-in in progress: leave it (and theirs) alone.
        raise _SignInFailed("This sign-in has expired or was already used. Start again.")
    try:
        try:
            oauth_config = await user_oauth_config(
                connector_id, config_service=config_service, org_id=str(current_org_id()), logger=logger,
                container=request.app.container,
            )
        except NotionAccessError as e:
            raise _SignInFailed(str(e)) from None
        provider = OAuthProvider(oauth_config, config_service, session)
        try:
            # Checks the state against the one stored for this caller only.
            token = await provider.handle_callback(code, state)
        finally:
            await provider.close()
        try:
            await _connect(
                request, app, connector_id, user, token.access_token,
                method=METHOD_OAUTH, refresh_token=token.refresh_token,
            )
        except HTTPException as e:
            raise _SignInFailed(str(e.detail), shareable=True) from None
    finally:
        # The session also holds the token the exchange wrote; the connection is its only home.
        await delete_config_confirmed(config_service, session)
    return app


@user_access_router.delete(
    "/api/v1/connectors/{connector_id}/user-access",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))],
)
async def disconnect_my_account(
    connector_id: str, request: Request, graph_provider: IGraphDBProvider = Depends(get_graph_provider)
) -> dict[str, Any]:
    app = await _per_user_connector(request, connector_id, graph_provider)
    user = await _caller(request, graph_provider)
    await _remove(request, graph_provider, connector_id, str(user.get("userId")), str(user.get("id") or user.get("_key")))
    return _status(None, app["config"])


@user_access_router.get(
    "/api/v1/connectors/{connector_id}/user-access/users",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))],
)
async def list_connected_users(
    connector_id: str, request: Request, graph_provider: IGraphDBProvider = Depends(get_graph_provider)
) -> dict[str, Any]:
    if not is_request_admin(request):
        raise _not_found()
    await _per_user_connector(request, connector_id, graph_provider)
    connections = await list_connections(_config_service(request), connector_id)
    return {"connections": [c.public() for c in sorted(connections, key=lambda c: c.email)]}


@user_access_router.delete(
    "/api/v1/connectors/{connector_id}/user-access/users/{user_id}",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))],
)
async def remove_connected_user(
    connector_id: str, user_id: str, request: Request, graph_provider: IGraphDBProvider = Depends(get_graph_provider)
) -> dict[str, Any]:
    if not is_request_admin(request):
        raise _not_found()
    await _per_user_connector(request, connector_id, graph_provider)
    target = await get_user_or_503(graph_provider, user_id)
    user_key = str((target or {}).get("id") or (target or {}).get("_key") or "")
    await _remove(request, graph_provider, connector_id, user_id, user_key)
    return {"removed": True}


async def _remove(
    request: Request, graph_provider: IGraphDBProvider, connector_id: str, user_id: str, user_key: str
) -> None:
    """Credential first: once it is gone no sync can grant the user anything again. A sign-in
    still waiting for its callback goes too, so it cannot connect the user after they left."""
    config_service = _config_service(request)
    async with refresh_lock(connector_id, user_id):
        removed = await delete_connection(config_service, connector_id, user_id)
    if not removed or not await delete_config_confirmed(config_service, sign_in_path(connector_id, user_id)):
        raise HTTPException(
            status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value, detail="The connection could not be removed. Try again."
        )
    await _drop_links(config_service, connector_id, user_id)
    if user_key:
        await _withdraw(graph_provider, connector_id, user_key)


# ==================== Roster and invites (admin) ====================


def _person_status(user_id: str | None, connection: UserConnection | None, invite: Invite | None) -> str:
    if connection is not None:
        return "connected" if connection.status == STATUS_ACTIVE else "needs_reconnect"
    if not user_id:
        return "no_account"
    if invite is not None and invite.opted_out:
        return "opted_out"
    if invite is not None and invite.send_count > 0:
        return "invited"
    return "not_invited"


async def _roster(request: Request, graph_provider: IGraphDBProvider, connector_id: str) -> dict[str, Any]:
    """Everyone the source lists (by the last sync) and everyone connected, each with their PipesHub
    account (matched by email), connection and invite."""
    org_id = str(current_org_id())
    config_service = _config_service(request)
    members, updated_at = await get_members(config_service, connector_id)
    connections = {c.user_id: c for c in await list_connections(config_service, connector_id)}
    invites = await list_invites(config_service, connector_id)
    # Raises on a failed read: an empty answer would show everyone as having no account.
    try:
        found = await graph_provider.get_users_by_emails(
            [str(m["email"]) for m in members], raise_on_error=True, org_id=org_id
        )
    except Exception as e:
        raise HTTPException(
            status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value, detail="Could not read the organisation's users."
        ) from e
    users = {
        email.lower(): {"userId": u.user_id, "email": u.email, "fullName": u.full_name, "orgId": u.org_id,
                        "isActive": u.is_active}
        for email, u in found.items()
        if u.user_id and u.is_active is not False
    }

    people: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(name: str | None, email: str, source_user_id: str | None, user: dict[str, Any] | None) -> None:
        user_id = str(user["userId"]) if user else None
        connection = connections.get(user_id) if user_id else None
        invite = invites.get(user_id) if user_id else None
        people.append({
            "name": name or (user or {}).get("fullName") or None,
            "email": email,
            "sourceUserId": source_user_id,
            "userId": user_id,
            "status": _person_status(user_id, connection, invite),
            "connection": connection.public() if connection else None,
            "invite": invite.public() if invite else None,
        })
        if user_id:
            seen.add(user_id)

    for member in members:
        email = str(member["email"])
        add(member.get("name"), email, member.get("sourceUserId"), users.get(email.lower()))
    # Connected or invited people the member list does not show (yet).
    for user_id in dict.fromkeys([*connections, *invites]):
        if user_id in seen:
            continue
        user = await get_user_or_503(graph_provider, user_id)
        if not _is_member(user, org_id):
            continue
        connection = connections.get(user_id)
        add(None, str(user["email"]), connection.source_user_id if connection else None, user)

    people.sort(key=lambda p: (_STATUS_ORDER.index(p["status"]), (p["name"] or p["email"]).lower()))
    counts = dict.fromkeys(_STATUS_ORDER, 0)
    for person in people:
        counts[person["status"]] += 1
    return {"membersUpdatedAt": updated_at, "people": people, "counts": counts}


@user_access_router.get(
    "/api/v1/connectors/{connector_id}/user-access/roster",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))],
)
async def get_roster(
    connector_id: str, request: Request, graph_provider: IGraphDBProvider = Depends(get_graph_provider)
) -> dict[str, Any]:
    if not is_request_admin(request):
        raise _not_found()
    await _per_user_connector(request, connector_id, graph_provider)
    return await _roster(request, graph_provider, connector_id)


@user_access_router.post(
    "/api/v1/connectors/{connector_id}/user-access/invites",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))],
)
async def send_invites(
    connector_id: str,
    request: Request,
    *,
    user_ids: list[str] | None = Body(None, alias="userIds", embed=True, max_length=_MAX_INVITES),
    all_people: bool = Body(False, alias="all", embed=True),
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
) -> dict[str, Any]:
    """Ask people to connect: everyone not connected who did not opt out (``all``), or the given users."""
    if not is_request_admin(request):
        raise _not_found()
    if bool(user_ids) == bool(all_people):
        raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail="Give either userIds or all.")
    app = await _per_user_connector(request, connector_id, graph_provider)
    admin = await _caller(request, graph_provider)
    org_id = str(current_org_id())
    config_service = _config_service(request)
    connections = {c.user_id: c for c in await list_connections(config_service, connector_id)}
    invites = await list_invites(config_service, connector_id)

    now = get_epoch_timestamp_in_ms()
    skipped: list[dict[str, str]] = []
    targets: list[dict[str, Any]] = []
    if all_people:
        roster = await _roster(request, graph_provider, connector_id)
        wanted = [p["userId"] for p in roster["people"] if p["status"] in ("not_invited", "invited", "needs_reconnect")]
    else:
        wanted = list(dict.fromkeys(str(u) for u in user_ids or [] if u))
    for user_id in wanted:
        connection = connections.get(user_id)
        invite = invites.get(user_id)
        if connection is not None and connection.status == STATUS_ACTIVE:
            skipped.append({"userId": user_id, "reason": "connected"})
            continue
        if all_people and invite is not None and invite.opted_out:
            skipped.append({"userId": user_id, "reason": "opted_out"})
            continue
        if all_people and invite is not None and invite.recently_sent(now):
            # A second click (or a retry after a timeout) must not send everyone the same email again.
            skipped.append({"userId": user_id, "reason": "recently_sent"})
            continue
        user = await get_user_or_503(graph_provider, user_id)
        if not _is_member(user, org_id):
            skipped.append({"userId": user_id, "reason": "not_found"})
            continue
        targets.append(user)

    org = await get_org_or_503(graph_provider, org_id)
    base = ConnectRequest(
        org_id=org_id, connector_id=connector_id, connector_name=str(app.get("name") or "Notion"),
        source_name=_SOURCE_NAMES.get(str(app.get("type")), str(app.get("type"))),
        user_id="", email="", inviter_name=admin.get("fullName") or None,
        org_name=(org or {}).get("name") or (org or {}).get("registeredName"),
    )
    send = _Sender(base, config_service, _notification_service(request), set(connections))
    if all_people:
        # One email at a time can take long; the admin's request must not wait for (and time out on) it.
        task = spawn_detached(_send_all(send, targets, org_id), name=f"connect-invites-{connector_id}")
        _BACKGROUND.add(task)  # the loop keeps only a weak reference to a task
        task.add_done_callback(_BACKGROUND.discard)
        return {"sent": len(targets), "emailed": None, "queued": True, "skipped": skipped}
    sent = emailed = 0
    for user in targets:
        notified, mailed = await send(user)
        if not (notified or mailed):
            skipped.append({"userId": str(user["userId"]), "reason": "not_sent"})
            continue
        sent += 1
        emailed += int(mailed)
    return {"sent": sent, "emailed": emailed, "queued": False, "skipped": skipped}


class _Sender:
    """Sends one person their connect request and records it on their invite."""

    def __init__(self, base: ConnectRequest, config_service: Any, notification_service: Any,  # noqa: ANN401
                 connected: set[str]) -> None:
        self.base, self.config_service, self.notification_service = base, config_service, notification_service
        self.connected = connected

    async def __call__(self, user: dict[str, Any]) -> tuple[bool, bool]:
        user_id = str(user["userId"])
        now = get_epoch_timestamp_in_ms()
        request = ConnectRequest(**{**self.base.__dict__, "user_id": user_id, "email": str(user["email"]),
                                    "reconnect": user_id in self.connected})
        notified, mailed = await send_connect_request(
            request, config_service=self.config_service, notification_service=self.notification_service,
            logger=logger, now_ms=now,
        )
        if notified or mailed:
            # Read again just before writing: the user may have opted out while this ran.
            invite = await get_invite(self.config_service, self.base.connector_id, user_id) or Invite(user_id=user_id)
            invite.sent(now)
            await save_invite(self.config_service, self.base.connector_id, invite)
        return notified, mailed


async def _send_all(send: _Sender, targets: list[dict[str, Any]], org_id: str) -> None:
    with bind(OrgContext(org_id, None, "service", "connect_invites", "-")):
        for user in targets:
            try:
                # The list was read before the first send: someone may have opted out since.
                invite = await get_invite(send.config_service, send.base.connector_id, str(user["userId"]))
                if invite is not None and invite.opted_out:
                    continue
                await send(user)
            except Exception as e:
                logger.warning("Could not send a connect request for connector %s: %s", send.base.connector_id, e)


def _notification_service(request: Request) -> Any:  # noqa: ANN401 - the container's provider is untyped
    try:
        return request.app.container.connector_notification_service()
    except Exception as e:
        logger.warning("No notification service for connect requests: %s", e)
        return None


@user_access_router.post(
    "/api/v1/connectors/{connector_id}/user-access/reminders",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))],
)
async def set_my_reminders(
    connector_id: str,
    request: Request,
    *,
    opted_out: bool = Body(..., alias="optedOut", embed=True),
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
) -> dict[str, Any]:
    """The caller's "don't remind me" for this connector."""
    await _per_user_connector(request, connector_id, graph_provider)
    user = await _caller(request, graph_provider)
    return {"optedOut": await _set_opted_out(_config_service(request), connector_id, str(user["userId"]), opted_out=opted_out)}


async def _set_opted_out(config_service: Any, connector_id: str, user_id: str, *, opted_out: bool) -> bool:  # noqa: ANN401
    invite = await get_invite(config_service, connector_id, user_id) or Invite(user_id=user_id)
    invite.opted_out = opted_out
    await save_invite(config_service, connector_id, invite)
    return opted_out


# ==================== Connect links (no PipesHub sign-in) ====================
# Excluded from the service's authentication (connectors_main.EXCLUDE_PATHS): the link is the credential.


@contextmanager
def _as_user(org_id: str, user_id: str) -> Iterator[None]:
    """The block runs in ``org_id``, for ``user_id``: the organisation a link (or its sign-in) names."""
    # "service", not "user": nothing here is a verified sign-in, so nothing may act as the user's session
    # (e.g. creating their graph record on first sight, which only a real sign-in may trigger).
    with bind(OrgContext(org_id, None, "service", "connect_link", "-")):
        yield


async def _link_user(graph_provider: IGraphDBProvider, org_id: str, user_id: str) -> dict[str, Any] | None:
    """The user, when an active member of ``org_id``. Call inside ``_as_user``."""
    user = await get_user_or_503(graph_provider, user_id)
    return user if _is_member(user, org_id) else None


async def _open_link(request: Request, graph_provider: IGraphDBProvider, token: str) -> ConnectLink:
    """The link. 404 for a link that is unknown or expired."""
    link = await resolve_link(_config_service(request), token.strip(), get_epoch_timestamp_in_ms())
    if link is None:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail=_LINK_GONE)
    return link


async def _link_target(
    request: Request, graph_provider: IGraphDBProvider, link: ConnectLink
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The link's user and connector, inside ``_as_user``. 404 when the user is no longer an active
    member or the connector no longer runs per user."""
    user = await _link_user(graph_provider, link.org_id, link.user_id)
    if user is None:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail=_LINK_GONE)
    try:
        app = await _per_user_connector(request, link.connector_id, graph_provider)
    except HTTPException:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail=_LINK_GONE) from None
    return user, app


@user_access_router.post("/api/v1/connectors/user-access/link/info")
async def connect_link_info(
    request: Request,
    token: str = Body(..., embed=True, min_length=1, max_length=_MAX_LINK_TOKEN_LENGTH),
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
) -> dict[str, Any]:
    link = await _open_link(request, graph_provider, token)
    with _as_user(link.org_id, link.user_id):
        user, app = await _link_target(request, graph_provider, link)
        connection = await get_connection(_config_service(request), link.connector_id, link.user_id)
    return {
        "connectorName": app.get("name"),
        "sourceType": _SOURCE_NAMES.get(str(app.get("type")), str(app.get("type"))),
        "email": user.get("email"),
        "connected": connection is not None and connection.status == STATUS_ACTIVE,
        "signInAvailable": has_oauth_app(app["config"]),
        "expiresAt": link.expires_at,
    }


@user_access_router.post("/api/v1/connectors/user-access/link/authorize")
async def connect_link_authorize(
    request: Request,
    token: str = Body(..., embed=True, min_length=1, max_length=_MAX_LINK_TOKEN_LENGTH),
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
) -> dict[str, Any]:
    link = await _open_link(request, graph_provider, token)
    with _as_user(link.org_id, link.user_id):
        await _link_target(request, graph_provider, link)
        url = await _authorization_url(request, link.connector_id, link.user_id, link_user=True)
    return {"authorizationUrl": url}


@user_access_router.post("/api/v1/connectors/user-access/link/complete")
async def connect_link_complete(
    request: Request,
    code: str = Body(..., embed=True, min_length=1, max_length=4000),
    state: str = Body(..., embed=True, min_length=1, max_length=4000),
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
) -> dict[str, Any]:
    """Finish a sign-in started from a connect link. The state names the user; it only completes
    when its random part matches the one stored for that user's sign-in. Never raises."""
    try:
        data = json.loads(base64.urlsafe_b64decode(state.encode() + b"=" * (-len(state) % 4)))
        connector_id = str(data["connector_id"])
        user_id, org_id = str(data[_LINK_USER_STATE_KEY]), str(data[_LINK_ORG_STATE_KEY])
        if not is_user_sign_in_state(data) or not connector_id or not user_id or not org_id:
            raise ValueError("not a connect-link sign-in")
    except Exception:
        return {"success": False, "error_message": "This sign-in link is not valid. Use the link in your email again."}
    try:
        with _as_user(org_id, user_id):
            user = await _link_user(graph_provider, org_id, user_id)
            if user is None:
                raise _SignInFailed(_LINK_GONE)
            app = await _complete_user_sign_in(
                request, graph_provider, user, connector_id=connector_id, state=str(data["state"]), code=code,
            )
    except Exception as e:
        if not isinstance(e, _SignInFailed):
            logger.warning("Notion sign-in from a connect link for connector %s failed: %s", connector_id, e)
        # The account checks (another person's or workspace's Notion account) are worth showing; anything
        # about whether a user, organisation or connector exists is not.
        message = e.user_message if isinstance(e, _SignInFailed) and e.shareable else (
            "Notion sign-in did not complete. Use the link in your email to try again.")
        return {"success": False, "error_message": message}
    return {"success": True, "connectorName": app.get("name")}


@user_access_router.post("/api/v1/connectors/user-access/link/opt-out")
async def connect_link_opt_out(
    request: Request,
    token: str = Body(..., embed=True, min_length=1, max_length=_MAX_LINK_TOKEN_LENGTH),
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
) -> dict[str, Any]:
    link = await _open_link(request, graph_provider, token)
    with _as_user(link.org_id, link.user_id):
        await _link_target(request, graph_provider, link)
        await _set_opted_out(_config_service(request), link.connector_id, link.user_id, opted_out=True)
    return {"optedOut": True}
