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
)

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


def classify_email_peer(keep_user_id: str, keep_key: str, peer: dict) -> str:
    """Return 'self', 'stub', or 'login' for a graph user that shares an email."""
    peer_key = graph_user_key(peer)
    if peer_key and peer_key == keep_key:
        return "self"
    peer_uid = str(peer.get("userId") or "").strip()
    if peer_uid and peer_uid == keep_user_id:
        return "self"
    if peer_uid and _MONGO_OBJECT_ID.fullmatch(peer_uid):
        return "login"
    return "stub"
