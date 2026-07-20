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

from .models import Actor

logger = logging.getLogger(__name__)

__all__ = ["AccessPolicy", "AccessDeniedError", "ArtifactNotFoundError"]


class AccessDeniedError(PermissionError):
    """Raised when `actor` is not authorized for the requested artifact."""


class ArtifactNotFoundError(LookupError):
    """Raised when the artifact/record does not exist (or its org does not
    match `actor.org_id` — deliberately indistinguishable from "not found"
    so a cross-org probe cannot use the error message to enumerate IDs)."""


class AccessPolicy:
    """Org + permission-edge checks, backend-agnostic via
    `IGraphDBProvider.get_edge`/`get_user_by_user_id` — works unchanged on
    ArangoDB and Neo4j (see `config/constants/neo4j.py`'s
    `COLLECTION_TO_LABEL`/`EDGE_COLLECTION_TO_RELATIONSHIP` mapping for
    `records`/`permission`)."""

    def __init__(self, graph_provider: Any) -> None:
        self._graph_provider = graph_provider

    async def resolve_user_key(self, actor: Actor) -> str:
        """`actor.user_id` is the external/auth `userId`; permission edges
        key off the internal `_key`. Raises `ArtifactNotFoundError` if the
        user record itself cannot be resolved — this should never happen
        for an authenticated request, but failing closed is the only safe
        default."""
        user_doc = await self._graph_provider.get_user_by_user_id(actor.user_id)
        if not user_doc:
            raise ArtifactNotFoundError(f"User not found for userId: {actor.user_id}")
        user_key = user_doc.get("_key") or user_doc.get("id")
        if not user_key:
            raise ArtifactNotFoundError(f"User record missing key for userId: {actor.user_id}")
        return user_key

    async def authorize_read(self, actor: Actor, artifact_id: str) -> dict:
        """Verify `actor` may read `artifact_id`'s base record. Returns the
        base `records` document on success.

        Owner (`role=OWNER` on a `USER -> RECORD` PERMISSION edge) is the
        only role artifacts support today — there is no share/collaborator
        flow yet, so read and write authorization are identical. Kept as
        two methods (not one) so a future share model only needs to change
        `authorize_read`'s edge-role check, not every call site."""
        return await self._authorize(actor, artifact_id)

    async def authorize_write(self, actor: Actor, artifact_id: str) -> dict:
        """Verify `actor` may create a new version of `artifact_id`. See
        `authorize_read`'s docstring for why this is currently identical."""
        return await self._authorize(actor, artifact_id)

    async def _authorize(self, actor: Actor, artifact_id: str) -> dict:
        record = await self._graph_provider.get_document(artifact_id, CollectionNames.RECORDS.value)
        if not record:
            raise ArtifactNotFoundError(f"Artifact not found: {artifact_id}")
        if record.get("orgId") != actor.org_id:
            # Same message as "not found" — never leak cross-org existence.
            raise ArtifactNotFoundError(f"Artifact not found: {artifact_id}")

        user_key = await self.resolve_user_key(actor)
        edge = await self._graph_provider.get_edge(
            from_id=user_key,
            from_collection=CollectionNames.USERS.value,
            to_id=artifact_id,
            to_collection=CollectionNames.RECORDS.value,
            collection=CollectionNames.PERMISSION.value,
        )
        if not edge:
            logger.warning(
                "Access denied: user=%s has no permission edge for artifact=%s",
                actor.user_id, artifact_id,
            )
            raise AccessDeniedError(f"Not authorized for artifact: {artifact_id}")
        return record

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
