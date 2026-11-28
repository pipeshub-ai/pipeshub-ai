"""`AccessPolicy` — the ONE place every artifact-registry operation checks
authorization. `ArtifactRegistryService` never touches `graph_provider`
permission edges directly; every method routes through here first so a
future permission model change (e.g. team-shared artifacts) is a one-file
change.

Model-supplied artifact IDs are always untrusted input — see `Actor`'s
docstring — so every lookup here re-verifies both org match AND a
`PERMISSION` edge for the requesting user, never trusting that an ID
"looks right".
"""

from __future__ import annotations

import logging
from typing import Any

from app.config.constants.arangodb import CollectionNames
from app.modules.authz.chat_content_access import (
    ChatContentAccess,
    get_chat_content_access,
)
from app.utils.user_lookup import get_caller_user

from .models import Actor

logger = logging.getLogger(__name__)

__all__ = [
    "ARTIFACT_SHARE_GRANT",
    "SHARED_VERSION",
    "AccessPolicy",
    "AccessDeniedError",
    "ArtifactNotFoundError",
    "ArtifactNotVisibleError",
    "is_direct_share",
    "reader_edge",
]

# A user's READER edge on an artifact can come from a shared conversation, from
# the owner sharing the artifact itself, or both, and there is only one edge per
# (user, record). An untagged READER edge is a conversation grant alone. A direct
# share sets `grantedVia` to this value, and `chatGranted` when a conversation
# grant covers the same pair too, so taking either grant away keeps the other.
ARTIFACT_SHARE_GRANT = "ARTIFACT_SHARE"
# On a direct share: the artifact's version when the owner shared it, the one the share was checked on.
SHARED_VERSION = "sharedVersion"


def is_direct_share(edge: dict | None) -> bool:
    return bool(edge) and edge.get("role") == "READER" and edge.get("grantedVia") == ARTIFACT_SHARE_GRANT


def reader_edge(
    user_key: str,
    record_id: str,
    *,
    direct: bool,
    chat: bool,
    now: int,
    created_at: int | None = None,
    shared_version: int | None = None,
) -> dict[str, Any]:
    """The READER edge for whichever grants remain. Unset flags are written as
    None, not left out: Arango merges an upserted edge's properties, so an omitted
    flag would keep its old value."""
    return {
        "from_id": user_key,
        "from_collection": CollectionNames.USERS.value,
        "to_id": record_id,
        "to_collection": CollectionNames.RECORDS.value,
        "type": "USER",
        "role": "READER",
        "grantedVia": ARTIFACT_SHARE_GRANT if direct else None,
        "chatGranted": True if direct and chat else None,
        SHARED_VERSION: shared_version if direct else None,
        "createdAtTimestamp": created_at or now,
        "updatedAtTimestamp": now,
    }


def _current_version(record: dict) -> int:
    version = record.get("version")
    return version if isinstance(version, int) and not isinstance(version, bool) and version >= 1 else 1


class AccessDeniedError(PermissionError):
    """Raised when `actor` is not authorized for the requested artifact."""


class ArtifactNotFoundError(LookupError):
    """Raised when the artifact/record does not exist (or its org does not
    match `actor.org_id` — deliberately indistinguishable from "not found"
    so a cross-org probe cannot use the error message to enumerate IDs)."""


class ArtifactNotVisibleError(ArtifactNotFoundError, AccessDeniedError):
    """A user of the artifact's org holds no permission edge on it (SEC-AZ-07).
    It is a not-found to every caller that maps errors to answers, so a guessed
    or leaked id tells a colleague no more than it tells another org; it stays
    an `AccessDeniedError` for the callers that refuse on either."""


class AccessPolicy:
    """Org + permission-edge checks, backend-agnostic via
    `IGraphDBProvider.get_edge`/`get_user_by_user_id` — works unchanged on
    ArangoDB and Neo4j (see `config/constants/neo4j.py`'s
    `COLLECTION_TO_LABEL`/`EDGE_COLLECTION_TO_RELATIONSHIP` mapping for
    `records`/`permission`)."""

    def __init__(self, graph_provider: Any, chat_access: ChatContentAccess | None = None) -> None:
        self._graph_provider = graph_provider
        self._chat_access = chat_access

    async def resolve_user_key(self, actor: Actor) -> str:
        """`actor.user_id` is the external/auth `userId`; permission edges
        key off the internal `_key`. Raises `ArtifactNotFoundError` if the
        user record itself cannot be resolved — this should never happen
        for an authenticated request, but failing closed is the only safe
        default."""
        user_doc = await get_caller_user(self._graph_provider, actor.user_id)
        if not user_doc:
            raise ArtifactNotFoundError(f"User not found for userId: {actor.user_id}")
        user_key = user_doc.get("_key") or user_doc.get("id")
        if not user_key:
            raise ArtifactNotFoundError(f"User record missing key for userId: {actor.user_id}")
        return user_key

    async def authorize_read(self, actor: Actor, artifact_id: str, *, version: int | None = None) -> dict:
        """Verify `actor` may read `artifact_id`'s base record, at `version`
        (its current version when None). Returns the base `records` document on
        success. Any `USER -> RECORD` PERMISSION edge suffices (OWNER for the
        creator, or a READER grant unless the installed `ChatContentAccess`
        says graph edges do not grant chat content); without one, that
        `ChatContentAccess` decides."""
        record, _ = await self._authorize(actor, artifact_id, require_owner=False, version=version)
        return record

    async def authorize_version(
        self, actor: Actor, artifact_id: str, version: int | None = None,
    ) -> tuple[dict, int]:
        """`authorize_read` that also says which version to give `actor`:
        `version` itself, or with none asked the newest one they may open,
        which for a reader `ChatContentAccess` decides can be older than the
        current one. Raises as `authorize_read` does when they may open none."""
        record, served = await self._authorize(actor, artifact_id, require_owner=False, version=version, newest=True)
        return record, served if served is not None else _current_version(record)

    async def authorize_version_for_every_member(self, actor: Actor, artifact_id: str) -> tuple[dict, int]:
        """`authorize_version` for every member of `actor`'s chat at once: the record, and the newest version all of
        them may open, which the installed `ChatContentAccess` decides (no one's edge, its owner's included, counts
        here). Raises as `authorize_read` does when they may open none."""
        record = await self._record(actor, artifact_id)
        current = _current_version(record)
        access = self._chat_access or get_chat_content_access()
        opened = [
            v for v in await access.versions_every_member_may_open(
                self._graph_provider, user_id=actor.user_id, org_id=actor.org_id, record=record,
                conversation_id=actor.conversation_id, acl_version=actor.acl_version,
            )
            if 1 <= v <= current
        ]
        if not opened:
            raise AccessDeniedError(f"Not every member of the chat may open artifact: {artifact_id}")
        return record, opened[-1]

    async def readable_versions(self, actor: Actor, artifact_id: str) -> tuple[dict, list[int]]:
        """The record and every version of it `actor` may open, oldest first.
        Raises as `authorize_read` does when they may open none."""
        record = await self._record(actor, artifact_id)
        current = _current_version(record)
        if await self._grants_by_edge(actor, artifact_id):
            return record, list(range(1, current + 1))
        access = self._chat_access or get_chat_content_access()
        readable = [
            v for v in await access.readable_chat_versions(
                self._graph_provider, user_id=actor.user_id, org_id=actor.org_id, record=record,
                conversation_id=actor.conversation_id, acl_version=actor.acl_version,
            )
            if 1 <= v <= current
        ]
        if not readable:
            logger.warning("Access denied: user=%s may open no version of artifact=%s", actor.user_id, artifact_id)
            raise AccessDeniedError(f"Not authorized for artifact: {artifact_id}")
        return record, readable

    async def authorize_write(self, actor: Actor, artifact_id: str) -> dict:
        """Verify `actor` may create a new version of, or promote, `artifact_id`.
        Requires an OWNER edge, so a READER from a shared conversation cannot
        overwrite the owner's artifact through `update_artifact` and friends."""
        record, _ = await self._authorize(actor, artifact_id, require_owner=True)
        return record

    async def _record(self, actor: Actor, artifact_id: str) -> dict:
        record = await self._graph_provider.get_document(artifact_id, CollectionNames.RECORDS.value)
        if not record:
            raise ArtifactNotFoundError(f"Artifact not found: {artifact_id}")
        if record.get("orgId") != actor.org_id:
            # Same message as "not found" — never leak cross-org existence.
            raise ArtifactNotFoundError(f"Artifact not found: {artifact_id}")
        return record

    async def opens_every_version(self, actor: Actor, artifact_id: str) -> bool:
        """Whether `actor` opens every version of `artifact_id` through their own
        PERMISSION edge (the owner; any reader where graph edges grant chat
        content), rather than version by version through `ChatContentAccess`."""
        return await self._grants_by_edge(actor, artifact_id)

    async def _grants_by_edge(self, actor: Actor, artifact_id: str) -> bool:
        """Whether `actor`'s own PERMISSION edge lets them read every version: the
        OWNER edge, or any edge where graph edges grant chat content."""
        user_key = await self.resolve_user_key(actor)
        edge = await self._graph_provider.get_edge(
            from_id=user_key,
            from_collection=CollectionNames.USERS.value,
            to_id=artifact_id,
            to_collection=CollectionNames.RECORDS.value,
            collection=CollectionNames.PERMISSION.value,
        )
        access = self._chat_access or get_chat_content_access()
        return bool(edge) and (edge.get("role") == "OWNER" or access.graph_grants_chat_content)

    async def _authorize(
        self, actor: Actor, artifact_id: str, *, require_owner: bool,
        version: int | None = None, newest: bool = False,
    ) -> tuple[dict, int | None]:
        """The record, and the version to serve: `version` as asked (None for the
        current one) unless `newest` lets a reader `ChatContentAccess` decides
        be given the newest version they may open."""
        record = await self._record(actor, artifact_id)

        user_key = await self.resolve_user_key(actor)
        edge = await self._graph_provider.get_edge(
            from_id=user_key,
            from_collection=CollectionNames.USERS.value,
            to_id=artifact_id,
            to_collection=CollectionNames.RECORDS.value,
            collection=CollectionNames.PERMISSION.value,
        )
        access = self._chat_access or get_chat_content_access()
        if not require_owner and edge and edge.get("role") != "OWNER" and not access.graph_grants_chat_content:
            edge = None  # only the creator's edge grants; `access` decides every other reader
        if not edge:
            if not require_owner:
                lookup = {
                    "user_id": actor.user_id,
                    "org_id": actor.org_id,
                    "record": record,
                    "conversation_id": actor.conversation_id,
                    "acl_version": actor.acl_version,
                }
                if await access.can_read_chat_content(self._graph_provider, **lookup, version=version):
                    return record, version
                if newest and version is None:
                    # The current version is not theirs: the newest one that is.
                    readable = await access.readable_chat_versions(self._graph_provider, **lookup)
                    if readable:
                        return record, readable[-1]
            logger.warning(
                "Access denied: user=%s has no permission edge for artifact=%s",
                actor.user_id, artifact_id,
            )
            raise ArtifactNotVisibleError(f"Artifact not found: {artifact_id}")
        if require_owner and edge.get("role") != "OWNER":
            logger.warning(
                "Write denied: user=%s holds role=%s (not OWNER) on artifact=%s",
                actor.user_id, edge.get("role"), artifact_id,
            )
            raise AccessDeniedError(f"Not authorized to modify artifact: {artifact_id}")
        return record, version

    async def grant_owner_permission(self, actor: Actor, artifact_id: str, *, now: int) -> dict:
        """Build (not persist) the `USER -> RECORD` OWNER permission edge
        for a newly created artifact — `registry.py` writes it in the same
        `batch_create_edges` call as the record creation so a partial
        write never leaves an artifact with no owner."""
        user_key = await self.resolve_user_key(actor)
        return {
            "from_id": user_key,
            "from_collection": CollectionNames.USERS.value,
            "to_id": artifact_id,
            "to_collection": CollectionNames.RECORDS.value,
            "type": "USER",
            "role": "OWNER",
            "createdAtTimestamp": now,
            "updatedAtTimestamp": now,
        }
