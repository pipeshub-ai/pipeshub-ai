"""`TieredRecordAuthorizer` — cheapest-first, no bypass.

Three tiers, each a real check:

1. Org scope: `record.org_id == actor.org_id`. O(1), always runs.
2. Direct permission-edge lookup: the same single `get_edge` call
   `AccessPolicy._authorize` already uses. Covers artifacts and user-uploaded
   chat attachments without touching the 10-path AQL union query.
   If `resolve_user_key` raises (actor has no user node), tier 2 is skipped
   — not treated as denial, just falls through to tier 3.
3. `check_record_access_with_details` as the completeness backstop, covering
   KB membership, team, group, record-group, org, and anyone-link paths.

Tiers are additive, not alternative — tier 3 only runs when tiers 1 and 2
both fail to confirm access. No bypass path exists; service-account callers
follow the same gate (see plan §Authorization — Open decision).
"""

from __future__ import annotations

import logging
from typing import Any

from app.config.constants.arangodb import CollectionNames
from app.services.artifact_registry.access import ArtifactNotFoundError

from .models import RecordAccessDeniedError

logger = logging.getLogger(__name__)

__all__ = ["TieredRecordAuthorizer"]


class TieredRecordAuthorizer:
    """The authorization gate for the record-content kernel.

    Constructed once per resolver instance; thread-safe and stateless
    beyond the injected graph_provider.
    """

    def __init__(self, graph_provider: Any) -> None:
        self._graph = graph_provider

    async def authorize(self, actor: Any, record: Any) -> None:
        """Authorize `actor` to read `record`. Raises `RecordAccessDeniedError`
        on denial; returns `None` on success.

        Never raises for reasons other than a confirmed denial — I/O errors
        propagate as their own exception types.
        """
        # Tier 1 — org scope (O(1), always runs)
        record_org = getattr(record, "org_id", None) or (
            record.get("orgId") if isinstance(record, dict) else None
        )
        if record_org and record_org != actor.org_id:
            raise RecordAccessDeniedError(
                f"Record does not belong to org {actor.org_id}"
            )

        record_id = getattr(record, "id", None) or (
            record.get("_key") if isinstance(record, dict) else None
        )

        # Tier 2 — direct permission-edge point lookup
        tier2_granted = await self._try_direct_edge(actor, record_id)
        if tier2_granted:
            return

        # Tier 3 — full ACL union query (KB, team, group, record-group, org, anyone-link)
        tier3_result = await self._graph.check_record_access_with_details(
            actor.user_id, actor.org_id, record_id
        )
        if tier3_result is not None:
            return

        logger.warning(
            "access_denied actor=%s record=%s",
            actor.user_id,
            record_id,
        )
        raise RecordAccessDeniedError(
            f"Actor {actor.user_id} is not authorized to read record {record_id}"
        )

    async def _try_direct_edge(self, actor: Any, record_id: str | None) -> bool:
        """Attempt tier-2 via a single `get_edge` call. Returns True if the
        actor has a direct permission edge to the record. Returns False (rather
        than raising) when:
        - `actor.user_id` has no graph user node (service account with no node)
        - the permission edge does not exist
        Any unexpected I/O error propagates to the caller.
        """
        if not record_id:
            return False

        try:
            from app.services.artifact_registry.access import AccessPolicy
            access = AccessPolicy(self._graph)
            user_key = await access.resolve_user_key(actor)
        except ArtifactNotFoundError:
            # Actor has no graph user node — skip tier 2, let tier 3 decide.
            return False

        edge = await self._graph.get_edge(
            from_id=user_key,
            from_collection=CollectionNames.USERS.value,
            to_id=record_id,
            to_collection=CollectionNames.RECORDS.value,
            collection=CollectionNames.PERMISSION.value,
        )
        return edge is not None
