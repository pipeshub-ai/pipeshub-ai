"""Per-user access for the team Notion connector.

Notion's API has no page permission list, so in per-user mode each user connects their own
Notion account, and a page reaches a user only when their own credential lists it. This module
holds the pieces that do not need the connector: which token a call runs with, the grant a pass
writes, and who a token belongs to.
"""
from __future__ import annotations

import asyncio
import contextlib
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.connectors.core.base.token_service.oauth_service import (
    OAuthConfig,
    OAuthProvider,
    RefreshTokenInvalidError,
)
from app.connectors.core.base.user_access import (
    UserConnection,
    connection_path,
    delete_connection,
    get_connection,
    save_connection,
)
from app.models.entities import Record, RecordType
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.notion.notion import NotionClient, NotionRESTClientViaToken
from app.sources.external.notion.notion import NotionDataSource
from app.utils.oauth_config import get_oauth_config

if TYPE_CHECKING:
    from collections.abc import Iterator
    from logging import Logger

    from app.config.configuration_service import ConfigurationService

# The registry name (ConnectorBuilder("Notion")), which is what an app document stores as its type.
CONNECTOR_TYPE = "Notion"

_UNAUTHORIZED = 401
_BAD_REQUEST = 400
_NOT_FOUND = 404

# The connection whose credential the current task runs with. A context variable, not a
# connector attribute: a stream running beside a sync must never borrow the sync's token, or
# one user's listing could be read with another's and granted to the wrong person.
_ACTIVE: ContextVar[UserConnection | None] = ContextVar("notion_user_access_active", default=None)

# Records a pass grants to its user. Files are not here: they inherit from their page.
GRANTED_TYPES = frozenset({RecordType.WEBPAGE, RecordType.DATASOURCE})


def active_connection() -> UserConnection | None:
    return _ACTIVE.get()


@contextlib.contextmanager
def acting_as(connection: UserConnection) -> Iterator[None]:
    """Run the calls inside with this connection's credential."""
    token = _ACTIVE.set(connection)
    try:
        yield
    finally:
        _ACTIVE.reset(token)


def user_grant(connection: UserConnection) -> Permission:
    return Permission(email=connection.email, type=PermissionType.READ, entity_type=EntityType.USER)


def with_user_grant(
    record: Record, permissions: list[Permission], connection: UserConnection
) -> tuple[Record, list[Permission]]:
    """A page or data source a user's own listing returned: their grant, and no inheritance.

    Notion lets a sub-page be narrower than its parent, so a page that inherited would reach
    everyone who can open the parent. A placeholder is never visible and is left as it is.
    """
    if record.record_type in GRANTED_TYPES and not record.is_placeholder:
        record.inherit_permissions = False
        # Other users' grants come from their own passes; the grants a user lost are dropped by their pass.
        record.adds_permissions = True
        return record, [user_grant(connection)]
    return record, permissions


class NotionAccessError(ValueError):
    """A credential that cannot be connected; the message is shown to the user."""


@dataclass(frozen=True)
class NotionIdentity:
    """Who a Notion credential acts as. ``workspace_id`` is None when ``/users/me`` does not say."""

    user_id: str
    email: str | None
    workspace_id: str | None


def parse_identity(me: dict[str, Any]) -> NotionIdentity:
    """Read ``GET /v1/users/me``. A personal access token acts as a person; an OAuth token is a
    bot owned by a person. A bot owned by the workspace (an integration secret) is nobody's
    own view and is refused."""
    kind = me.get("type")
    if kind == "person" and me.get("id"):
        return NotionIdentity(str(me["id"]), _email(me), None)
    if kind == "bot":
        bot = me.get("bot") or {}
        owner = bot.get("owner") or {}
        user = owner.get("user") or {}
        if owner.get("type") == "user" and user.get("id"):
            return NotionIdentity(str(user["id"]), _email(user), bot.get("workspace_id"))
        raise NotionAccessError(
            "This is an integration secret, which is not tied to a person. "
            "Paste a personal access token created under your own Notion account."
        )
    raise NotionAccessError("Notion did not say who this token belongs to.")


def _email(user: dict[str, Any]) -> str | None:
    email = (user.get("person") or {}).get("email")
    return email.strip() if isinstance(email, str) and email.strip() else None


async def verify_token(
    token: str,
    *,
    pipeshub_email: str,
    connector_id: str,
    config_service: ConfigurationService,
    logger: Logger,
) -> NotionIdentity:
    """Who ``token`` acts as, after checking it may be connected to this connector by this person.

    Raises NotionAccessError for a credential that must be refused (shown to the user) and
    RuntimeError when Notion could not be asked (try again later)."""
    user_client = NotionClient(NotionRESTClientViaToken(token))
    admin_client = await NotionClient.build_from_services(
        logger=logger, config_service=config_service, connector_instance_id=connector_id, connector_type="notion",
    )
    try:
        me = await NotionDataSource(user_client).retrieve_bot_user()
        if me.status_code == _UNAUTHORIZED:
            raise NotionAccessError("Notion did not accept this token. Check that it was copied in full.")
        if not me.success or me.data is None:
            raise RuntimeError(f"Notion could not be asked who the token belongs to: {me.error}")
        identity = parse_identity(me.data.json())

        admin_me = await NotionDataSource(admin_client).retrieve_bot_user()
        workspace_id = ((admin_me.data.json() or {}).get("bot") or {}).get("workspace_id") if admin_me.success and admin_me.data else None
        if not workspace_id:
            raise RuntimeError(f"Could not read the connector's Notion workspace: {admin_me.error}")
        check_identity(identity, pipeshub_email=pipeshub_email, connector_workspace_id=workspace_id)

        if identity.workspace_id is None:
            # A personal access token names no workspace: the connector's own integration must know the person.
            # shortcut: a person's id is the same in every workspace they belong to, so a token made in another
            # workspace passes; what it lists is still granted only to its owner. Upgrade once /users/me shows it.
            member = await NotionDataSource(admin_client).retrieve_user(identity.user_id)
            if member.status_code in (_NOT_FOUND, _BAD_REQUEST):
                raise NotionAccessError("This Notion account is not a member of the connector's workspace.")
            if not member.success:
                raise RuntimeError(f"Could not check the account's workspace membership: {member.error}")
        return NotionIdentity(identity.user_id, identity.email, workspace_id)
    finally:
        for client in (user_client, admin_client):
            try:
                await client.get_client().close()
            except Exception as e:
                logger.warning("Failed to close a Notion client: %s", e)


def check_identity(
    identity: NotionIdentity, *, pipeshub_email: str, connector_workspace_id: str | None
) -> None:
    """Refuse a credential for another workspace or another person. A credential whose
    workspace ``/users/me`` does not name is checked against the workspace by the caller."""
    if identity.workspace_id and connector_workspace_id and identity.workspace_id != connector_workspace_id:
        raise NotionAccessError("This token belongs to a different Notion workspace than this connector.")
    if not identity.email:
        raise NotionAccessError(
            "Notion did not return the email of this account, so it cannot be matched to you."
        )
    if identity.email.casefold() != pipeshub_email.strip().casefold():
        raise NotionAccessError(
            f"This token belongs to {identity.email}. Connect the Notion account that uses your PipesHub email."
        )


def has_oauth_app(config: dict[str, Any] | None) -> bool:
    """Users can sign in with Notion when the connector itself uses a Notion OAuth app."""
    auth = (config or {}).get("auth") or {}
    return str(auth.get("authType") or "").upper() == "OAUTH"


async def user_oauth_config(
    connector_id: str,
    *,
    config_service: ConfigurationService,
    org_id: str,
    logger: Logger,
    container: Any = None,  # noqa: ANN401 - the app container is untyped
) -> OAuthConfig:
    """The connector's Notion OAuth app, for a user's own sign-in and its refreshes."""
    config = await config_service.get_config(
        f"/services/connectors/{connector_id}/config", use_cache=False, raise_on_error=True
    )
    if not has_oauth_app(config if isinstance(config, dict) else None):
        raise NotionAccessError(
            "This connector has no Notion OAuth app to sign in with. Paste a personal access token instead."
        )
    # Imported here: the router imports the connectors, this module among them.
    from app.connectors.api.router import _build_oauth_flow_config

    flow = await _build_oauth_flow_config(
        auth_config=config["auth"], connector_type=CONNECTOR_TYPE.upper(), org_id=org_id,
        config_service=config_service, logger=logger, container=container, registry_type=CONNECTOR_TYPE,
    )
    return get_oauth_config(flow)


# One refresh at a time per connection in this process: Notion rotates the refresh token, so a
# second refresh with the same one is refused and would look like a revoked sign-in.
_REFRESH_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}


def refresh_lock(connector_id: str, user_id: str) -> asyncio.Lock:
    """Held while a connection's token is refreshed; removing the connection takes it too, so a refresh
    that read the connection cannot write it back after it was deleted.
    shortcut: per process; a sync in another process can still race a removal, use a store lock then."""
    return _REFRESH_LOCKS.setdefault((connector_id, user_id), asyncio.Lock())


async def refresh_connection(
    connection: UserConnection,
    *,
    connector_id: str,
    config_service: ConfigurationService,
    org_id: str,
    logger: Logger,
) -> bool | None:
    """After Notion refused ``connection``'s token: get a live one into ``connection`` (in place,
    so a task already acting as it uses the new token).

    True: ``connection`` holds a newer token. False: there is none to be had (no refresh token,
    Notion refused it, or the user disconnected); the user must connect again. None: the refresh
    could not be tried or failed for another reason; try again later.
    """
    async with refresh_lock(connector_id, connection.user_id):
        stored = await get_connection(config_service, connector_id, connection.user_id)
        if stored is None or stored.connected_at != connection.connected_at:
            return False  # disconnected, or connected again: this connection is over
        if stored.token != connection.token:
            _adopt(connection, stored)  # another task already refreshed it
            return True
        if not stored.refresh_token:
            return False
        try:
            oauth_config = await user_oauth_config(
                connector_id, config_service=config_service, org_id=org_id, logger=logger
            )
            # The provider stores the new pair under ``credentials`` of the connection before
            # returning, so a rotated refresh token is never lost (UserConnection.from_dict reads it).
            provider = OAuthProvider(oauth_config, config_service, connection_path(connector_id, connection.user_id))
            try:
                token = await provider.refresh_access_token(stored.refresh_token)
            finally:
                await provider.close()
        except RefreshTokenInvalidError:
            # Refused: unless another process refreshed it meanwhile, the sign-in is over.
            latest = await get_connection(config_service, connector_id, connection.user_id)
            if latest is not None and latest.connected_at == connection.connected_at and latest.token != stored.token:
                _adopt(connection, latest)
                return True
            return False
        except NotionAccessError:
            return False
        except Exception as e:
            logger.warning("Could not refresh the Notion sign-in of user %s: %s", connection.user_id, e)
            return None

        latest = await get_connection(config_service, connector_id, connection.user_id)
        if latest is None:
            # Disconnected while refreshing: the refresh must not leave a credential behind.
            await delete_connection(config_service, connector_id, connection.user_id)
            return False
        if latest.connected_at != connection.connected_at:
            # Connected again while refreshing: the new connection keeps its own token.
            await _drop_rotated(config_service, connector_id, connection.user_id)
            return False
        latest.token = token.access_token
        latest.refresh_token = token.refresh_token or stored.refresh_token
        await save_connection(config_service, connector_id, latest)
        _adopt(connection, latest)
        return True


async def _drop_rotated(config_service: ConfigurationService, connector_id: str, user_id: str) -> None:
    path = connection_path(connector_id, user_id)
    data = await config_service.get_config(path, use_cache=False, keep_in_cache=False, raise_on_error=True)
    if isinstance(data, dict) and "credentials" in data:
        del data["credentials"]
        await config_service.set_config(path, data, keep_in_cache=False)


def _adopt(connection: UserConnection, stored: UserConnection) -> None:
    connection.token, connection.refresh_token = stored.token, stored.refresh_token
