"""Per-user access: each user connects their own source account to a team connector.

A team connector in per-user mode shows each user only what the source returns to that user's
own credential. The admin's credential identifies the workspace and never grants anyone anything.
Each connection is kept next to the connector's config, one key per user, and is deleted with
the connector.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from typing import Any

from app.config.configuration_service import (
    ConfigurationService,
    delete_config_confirmed,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms

ACCESS_MODE_FIELD = "accessMode"
ACCESS_MODE_WORKSPACE = "workspace"
ACCESS_MODE_PER_USER = "per_user"

STATUS_ACTIVE = "active"
STATUS_REVOKED = "revoked"

# How the user connected: a token they pasted, or the source's own sign-in (OAuth).
METHOD_TOKEN = "token"
METHOD_OAUTH = "oauth"

# A user whose last complete listing is older than this loses access until one completes.
STALE_AFTER_MS = 48 * 60 * 60 * 1000


def connections_prefix(connector_id: str) -> str:
    return f"/services/connectors/{connector_id}/users/"


def connection_path(connector_id: str, user_id: str) -> str:
    return f"{connections_prefix(connector_id)}{user_id}"


def sign_in_prefix(connector_id: str) -> str:
    return f"/services/connectors/{connector_id}/sign-in/"


def sign_in_path(connector_id: str, user_id: str) -> str:
    """Where one user's OAuth sign-in keeps its state until the callback: per user, so two people
    signing in at once cannot overwrite each other's."""
    return f"{sign_in_prefix(connector_id)}{user_id}"


def invites_prefix(connector_id: str) -> str:
    return f"/services/connectors/{connector_id}/invites/"


def invite_path(connector_id: str, user_id: str) -> str:
    return f"{invites_prefix(connector_id)}{user_id}"


def links_prefix(connector_id: str) -> str:
    return f"/services/connectors/{connector_id}/links/"


def removals_prefix(connector_id: str) -> str:
    return f"/services/connectors/{connector_id}/removed/"


def removal_path(connector_id: str, user_id: str) -> str:
    """When the user's connection was last removed: a write of a connection made before then undoes itself."""
    return f"{removals_prefix(connector_id)}{user_id}"


def members_path(connector_id: str) -> str:
    """The source's member list as the last sync read it, for the admin's roster."""
    return f"/services/connectors/{connector_id}/members"


def access_mode(config: dict[str, Any] | None) -> str:
    """The connector's access mode from its sync config (flat, ``values`` or ``customValues``)."""
    sync = (config or {}).get("sync") or {}
    for source in (sync, sync.get("values"), sync.get("customValues")):
        if isinstance(source, dict) and source.get(ACCESS_MODE_FIELD):
            return str(source[ACCESS_MODE_FIELD])
    return ACCESS_MODE_WORKSPACE


def is_per_user(config: dict[str, Any] | None) -> bool:
    return access_mode(config) == ACCESS_MODE_PER_USER


@dataclass
class UserConnection:
    """One PipesHub user's credential for the connector's source.

    ``email`` is the PipesHub user's email: the grants a sync writes go to it. ``source_*`` name
    the source account the credential belongs to, checked when the user connected. A credential
    from the source's sign-in (OAuth) may also have a ``refresh_token``.
    """

    user_id: str
    email: str
    token: str
    source_user_id: str
    source_email: str
    workspace_id: str | None = None
    status: str = STATUS_ACTIVE
    connected_at: int = field(default_factory=get_epoch_timestamp_in_ms)
    last_complete_listing_at: int | None = None
    last_error: str | None = None
    refresh_token: str | None = None
    method: str = METHOD_TOKEN

    @property
    def active(self) -> bool:
        return self.status == STATUS_ACTIVE

    def stale(self, now_ms: int) -> bool:
        """No complete listing within ``STALE_AFTER_MS``: its stored view can no longer be trusted."""
        since = self.last_complete_listing_at or self.connected_at
        return now_ms - since > STALE_AFTER_MS

    def to_dict(self) -> dict[str, Any]:
        return {
            "userId": self.user_id,
            "email": self.email,
            "token": self.token,
            "sourceUserId": self.source_user_id,
            "sourceEmail": self.source_email,
            "workspaceId": self.workspace_id,
            "status": self.status,
            "connectedAt": self.connected_at,
            "lastCompleteListingAt": self.last_complete_listing_at,
            "lastError": self.last_error,
            "refreshToken": self.refresh_token,
            "method": self.method,
        }

    def public(self) -> dict[str, Any]:
        """What the API may return: everything but the credential."""
        data = self.to_dict()
        del data["token"], data["refreshToken"]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserConnection:
        # A refresh (OAuthProvider.refresh_access_token) stores the rotated pair under ``credentials``
        # first; that pair is the live one even when the save after it did not happen.
        rotated = data.get("credentials") if isinstance(data.get("credentials"), dict) else {}
        return cls(
            user_id=str(data["userId"]),
            email=str(data["email"]),
            token=str(rotated.get("access_token") or data["token"]),
            refresh_token=rotated.get("refresh_token") or data.get("refreshToken"),
            method=str(data.get("method") or METHOD_TOKEN),
            source_user_id=str(data.get("sourceUserId") or ""),
            source_email=str(data.get("sourceEmail") or ""),
            workspace_id=data.get("workspaceId"),
            status=str(data.get("status") or STATUS_ACTIVE),
            connected_at=int(data.get("connectedAt") or 0),
            last_complete_listing_at=data.get("lastCompleteListingAt"),
            last_error=data.get("lastError"),
        )


async def get_connection(
    config_service: ConfigurationService, connector_id: str, user_id: str
) -> UserConnection | None:
    """The user's connection, or None when they have none. A failed read raises."""
    data = await config_service.get_config(
        connection_path(connector_id, user_id), use_cache=False, keep_in_cache=False, raise_on_error=True
    )
    return UserConnection.from_dict(data) if isinstance(data, dict) and data.get("token") else None


async def save_connection(
    config_service: ConfigurationService, connector_id: str, connection: UserConnection
) -> bool:
    """Store the connection. False when it was removed after it was made (``mark_removed``): then the write is
    undone. The removal is marked before it deletes, and checked here after the write, so whichever process
    writes a stale connection, one of the two deletes it; the store has no conditional write to do it in one."""
    if not await config_service.set_config(
        connection_path(connector_id, connection.user_id), connection.to_dict(), keep_in_cache=False
    ):
        raise RuntimeError(f"Could not save the connection of user {connection.user_id} to {connector_id}")
    marker = await config_service.get_config(
        removal_path(connector_id, connection.user_id), use_cache=False, keep_in_cache=False, raise_on_error=True
    )
    removed_at = int(marker.get("removedAt") or 0) if isinstance(marker, dict) else 0
    if removed_at and removed_at >= connection.connected_at:
        await delete_connection(config_service, connector_id, connection.user_id)
        return False
    return True


async def mark_removed(config_service: ConfigurationService, connector_id: str, user_id: str, now_ms: int) -> None:
    """Record a removal before deleting the connection (see ``save_connection``)."""
    if not await config_service.set_config(
        removal_path(connector_id, user_id), {"removedAt": now_ms}, keep_in_cache=False
    ):
        raise RuntimeError(f"Could not record the removal of user {user_id} from {connector_id}")


async def delete_connection(config_service: ConfigurationService, connector_id: str, user_id: str) -> bool:
    """True when the connection is gone, also when there was none."""
    return await delete_config_confirmed(config_service, connection_path(connector_id, user_id))


async def list_connections(config_service: ConfigurationService, connector_id: str) -> list[UserConnection]:
    """Every user's connection to the connector. A failed read raises: an empty list is read as
    "nobody is connected", which takes everyone's access away."""
    connections: list[UserConnection] = []
    for key in await config_service.list_keys_in_directory(connections_prefix(connector_id)):
        data = await config_service.get_config(key, use_cache=False, keep_in_cache=False, raise_on_error=True)
        if isinstance(data, dict) and data.get("token"):
            connections.append(UserConnection.from_dict(data))
    return connections


async def delete_all_connections(config_service: ConfigurationService, connector_id: str) -> bool:
    """Delete every user's connection to the connector, their invites and connect links, and the
    member list, for a connector being deleted. True when all are gone."""
    gone = True
    for prefix in (
        connections_prefix(connector_id), sign_in_prefix(connector_id),
        invites_prefix(connector_id), links_prefix(connector_id), removals_prefix(connector_id),
    ):
        for key in await config_service.list_keys_in_directory(prefix):
            gone = await delete_config_confirmed(config_service, key) and gone
    return await delete_config_confirmed(config_service, members_path(connector_id)) and gone


# ==================== Invites ====================

_DAY_MS = 24 * 60 * 60 * 1000
# Reminders after the first invite: the second send once this much passed since the first, the third after this.
REMINDER_AFTER_MS = (3 * _DAY_MS, 10 * _DAY_MS)
# Never two sends to one person closer than this (a reminder, or a re-sent invite from "invite all").
MIN_SEND_GAP_MS = _DAY_MS


@dataclass
class Invite:
    """What a user was sent asking them to connect, and whether they asked for no more reminders."""

    user_id: str
    first_sent_at: int | None = None
    last_sent_at: int | None = None
    send_count: int = 0
    opted_out: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "userId": self.user_id,
            "firstSentAt": self.first_sent_at,
            "lastSentAt": self.last_sent_at,
            "sendCount": self.send_count,
            "optedOut": self.opted_out,
        }

    def public(self) -> dict[str, Any]:
        data = self.to_dict()
        del data["userId"]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Invite:
        return cls(
            user_id=str(data["userId"]),
            first_sent_at=data.get("firstSentAt"),
            last_sent_at=data.get("lastSentAt"),
            send_count=int(data.get("sendCount") or 0),
            opted_out=bool(data.get("optedOut")),
        )

    def sent(self, now_ms: int) -> None:
        if self.first_sent_at is None:
            self.first_sent_at = now_ms
        self.last_sent_at = now_ms
        self.send_count += 1

    def recently_sent(self, now_ms: int) -> bool:
        return self.last_sent_at is not None and now_ms - self.last_sent_at < MIN_SEND_GAP_MS

    def reminder_due(self, now_ms: int) -> bool:
        """A reminder is due once 3 and once 10 days after the first send, unless something was sent
        since that date (an admin's re-send counts), then never again; never within a day of the last send."""
        if self.opted_out or self.first_sent_at is None or self.last_sent_at is None or self.recently_sent(now_ms):
            return False
        dates = [self.first_sent_at + after for after in REMINDER_AFTER_MS]
        return any(self.last_sent_at < date <= now_ms for date in dates)


async def get_invite(config_service: ConfigurationService, connector_id: str, user_id: str) -> Invite | None:
    data = await config_service.get_config(
        invite_path(connector_id, user_id), use_cache=False, keep_in_cache=False, raise_on_error=True
    )
    return Invite.from_dict(data) if isinstance(data, dict) and data.get("userId") else None


async def save_invite(config_service: ConfigurationService, connector_id: str, invite: Invite) -> None:
    if not await config_service.set_config(
        invite_path(connector_id, invite.user_id), invite.to_dict(), keep_in_cache=False
    ):
        raise RuntimeError(f"Could not save the invite of user {invite.user_id} to {connector_id}")


async def delete_invite(config_service: ConfigurationService, connector_id: str, user_id: str) -> bool:
    return await delete_config_confirmed(config_service, invite_path(connector_id, user_id))


async def list_invites(config_service: ConfigurationService, connector_id: str) -> dict[str, Invite]:
    invites: dict[str, Invite] = {}
    for key in await config_service.list_keys_in_directory(invites_prefix(connector_id)):
        data = await config_service.get_config(key, use_cache=False, keep_in_cache=False, raise_on_error=True)
        if isinstance(data, dict) and data.get("userId"):
            invite = Invite.from_dict(data)
            invites[invite.user_id] = invite
    return invites


# ==================== Connect links ====================

# How long a connect link from an email works.
LINK_TTL_DAYS = 7
LINK_TTL_MS = LINK_TTL_DAYS * _DAY_MS
# Links a user may hold at once (the invite and its reminders); the oldest goes first.
_MAX_LINKS_PER_USER = 3
_LINK_SEPARATOR = "."


def _link_key(connector_id: str, user_id: str) -> str:
    """All of one user's links, in one key: sending or removing them never reads anyone else's."""
    return f"{links_prefix(connector_id)}{user_id}"


def _hash(secret: str) -> str:
    # Only a hash is stored: whoever reads the store cannot rebuild a working link from it.
    return hashlib.sha256(secret.encode()).hexdigest()


@dataclass
class ConnectLink:
    """A link that lets one user connect their own account without signing in to PipesHub first.
    It names who they are; the sign-in still checks that the source account has their email."""

    connector_id: str
    user_id: str
    org_id: str
    expires_at: int


async def _user_links(config_service: ConfigurationService, connector_id: str, user_id: str) -> dict[str, Any]:
    data = await config_service.get_config(
        _link_key(connector_id, user_id), use_cache=False, keep_in_cache=False, raise_on_error=True
    )
    return data if isinstance(data, dict) and isinstance(data.get("links"), list) else {}


async def create_link(
    config_service: ConfigurationService, connector_id: str, user_id: str, org_id: str, now_ms: int
) -> str:
    """A new link token for ``user_id``: ``<connectorId>.<userId>.<secret>``. Their earlier links keep
    working until they expire, up to the newest few."""
    if not user_id or _LINK_SEPARATOR in user_id or "/" in user_id:
        raise ValueError("A connect link needs a plain user id")
    secret = secrets.token_urlsafe(32)
    current = await _user_links(config_service, connector_id, user_id)
    links = [x for x in current.get("links", []) if isinstance(x, dict) and int(x.get("expiresAt") or 0) > now_ms]
    links = [*links, {"hash": _hash(secret), "expiresAt": now_ms + LINK_TTL_MS}][-_MAX_LINKS_PER_USER:]
    record = {"userId": user_id, "orgId": org_id, "links": links}
    if not await config_service.set_config(_link_key(connector_id, user_id), record, keep_in_cache=False):
        raise RuntimeError(f"Could not save a connect link for user {user_id} to {connector_id}")
    return _LINK_SEPARATOR.join((connector_id, user_id, secret))


async def resolve_link(config_service: ConfigurationService, token: str, now_ms: int) -> ConnectLink | None:
    """The link ``token`` names, or None when it is malformed, unknown or expired."""
    parts = token.split(_LINK_SEPARATOR, 2)
    if len(parts) != 3 or not all(parts) or any("/" in p for p in parts[:2]):
        return None
    connector_id, user_id, secret = parts
    data = await _user_links(config_service, connector_id, user_id)
    if str(data.get("userId") or "") != user_id or not data.get("orgId"):
        return None
    wanted = _hash(secret)
    for link in data["links"]:
        if isinstance(link, dict) and secrets.compare_digest(str(link.get("hash") or ""), wanted):
            expires_at = int(link.get("expiresAt") or 0)
            if expires_at <= now_ms:
                return None
            return ConnectLink(connector_id, user_id, str(data["orgId"]), expires_at)
    return None


async def delete_user_links(config_service: ConfigurationService, connector_id: str, user_id: str) -> bool:
    """Delete every connect link of ``user_id`` (once they connected, or were removed)."""
    return await delete_config_confirmed(config_service, _link_key(connector_id, user_id))


# ==================== Member list ====================


async def save_members(
    config_service: ConfigurationService, connector_id: str, members: list[dict[str, Any]], now_ms: int
) -> None:
    """``members``: ``{"sourceUserId", "name", "email"}`` for each person the source lists with an email."""
    if not await config_service.set_config(
        members_path(connector_id), {"updatedAt": now_ms, "members": members}, keep_in_cache=False
    ):
        raise RuntimeError(f"Could not save the member list of {connector_id}")


async def get_members(
    config_service: ConfigurationService, connector_id: str
) -> tuple[list[dict[str, Any]], int | None]:
    """The member list the last sync read, and when; ``([], None)`` before any."""
    data = await config_service.get_config(
        members_path(connector_id), use_cache=False, keep_in_cache=False, raise_on_error=True
    )
    if not isinstance(data, dict):
        return [], None
    members = [m for m in data.get("members") or [] if isinstance(m, dict) and m.get("email")]
    return members, data.get("updatedAt")
