"""Classify graph users that share an email after a verified profile change."""

from __future__ import annotations

import re

from app.config.constants.arangodb import CollectionNames

_MONGO_OBJECT_ID = re.compile(r"^[a-fA-F0-9]{24}$")

STUB_EDGE_COLLECTIONS = (
    CollectionNames.PERMISSION.value,
    CollectionNames.BELONGS_TO.value,
    CollectionNames.USER_APP_RELATION.value,
    CollectionNames.USER_DRIVE_RELATION.value,
    CollectionNames.AUTHENTICATED_AS.value,
    CollectionNames.ENTITY_RELATIONS.value,
)

# One AUTHENTICATED_AS edge exists per connector, so connectorId is part of the
# edge's identity: two connectors linking the same two users must both survive.
# ENTITY_RELATIONS holds CREATED_BY / ASSIGNED_TO / REPORTED_BY on the same
# record/user pair, so edgeType must be part of the identity as well.
STUB_EDGE_IDENTITY_FIELDS: dict[str, tuple[str, ...]] = {
    CollectionNames.AUTHENTICATED_AS.value: ("connectorId",),
    CollectionNames.ENTITY_RELATIONS.value: ("edgeType",),
}

# When the stub and the login already hold a PERMISSION to the same node, the
# stronger role is kept; roles not listed rank lowest.
PERMISSION_ROLE_RANK: dict[str, int] = {
    "OWNER": 6,
    "ADMIN": 5,
    "ORGANIZER": 5,
    "EDITOR": 4,
    "FILEORGANIZER": 4,
    "WRITER": 3,
    "COMMENTER": 2,
    "READER": 1,
}

VERIFIED_EMAIL_WRITE_COLLECTIONS = (
    CollectionNames.USERS.value,
    *STUB_EDGE_COLLECTIONS,
)


class GraphUserEmailConflictError(Exception):
    """Another real login graph user already owns this email."""

    def __init__(self, message: str, *, conflicting_user_id: str | None = None) -> None:
        super().__init__(message)
        self.conflicting_user_id = conflicting_user_id


def graph_user_key(user: dict) -> str | None:
    key = user.get("id") or user.get("_key")
    return str(key) if key else None


async def connector_ids_of_users(provider, user_keys: list[str]) -> list[str]:
    """Connectors the given graph users belong to, read before they are merged away.

    Best-effort: the result only drives cache invalidation, so a lookup failure
    must not fail the email change.
    """
    connector_ids: set[str] = set()
    for user_key in user_keys:
        try:
            apps = await provider.get_user_apps(user_key)
        except Exception as e:
            provider.logger.warning("Could not list connectors of graph user %s: %s", user_key, e)
            continue
        for app in apps or []:
            connector_id = app.get("id") or app.get("_key")
            if connector_id:
                connector_ids.add(str(connector_id))
    return sorted(connector_ids)


def classify_email_peer(keep_user_id: str, keep_key: str, peer: dict) -> str:
    """Return 'self', 'stub', or 'login' for a graph user that shares an email."""
    peer_key = graph_user_key(peer)
    if peer_key and peer_key == keep_key:
        return "self"
    peer_uid = str(peer.get("userId") or "").strip()
    if peer_uid and peer_uid == keep_user_id:
        return "self"
    # Connector stubs are created inactive and carry the source account id as
    # userId, which for older Atlassian accounts is also 24 hex characters.
    # Real logins are always written active, so only an explicit False clears it.
    if peer_uid and _MONGO_OBJECT_ID.fullmatch(peer_uid) and peer.get("isActive") is not False:
        return "login"
    return "stub"
