"""`AuditGovernor`: the minimal-code Phase-4 governance seam from the plan
— wraps another `SkillGovernor` (composition, not inheritance, so the
underlying approve/reject *policy* — `AutoApproveGovernor` or
`ManualReviewGovernor` — is unchanged) and additionally appends a capped
audit-trail entry onto the skill's own `agentSkills` document whenever a
skill is created or deprecated.

The trail is stored as four index-aligned primitive arrays
(`auditActions`/`auditActorIds`/`auditReasons`/`auditTimestamps` — see
`app/schema/arango/documents.py`) rather than one array of objects:
Neo4j properties can only be primitives or arrays thereof, so an array
of maps works on Arango but throws on Neo4j. `auditReasons` uses "" for
"no reason" since Neo4j also rejects nulls inside arrays.

`SkillManager` needs zero changes for this: it already calls
`governor.on_skill_created`/`on_skill_deprecated` at exactly the right
points (see `manager.py`), so richer governance (approval workflows,
promotion, org policy) is purely a matter of swapping/layering
`SkillGovernor` implementations, per Open/Closed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.agent_loop_lib.modules.providers.skills.base import (
    SkillCandidate,
    SkillMetadata,
)
from app.agent_loop_lib.modules.providers.skills.governor import SkillGovernor
from app.config.constants.arangodb import CollectionNames
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = ["AuditGovernor"]

logger = logging.getLogger(__name__)

_SKILLS = CollectionNames.AGENT_SKILLS.value

# Bounds the trail per skill — this is a "who did what, when" breadcrumb,
# not a compliance-grade immutable log; full content history for any given
# revision already lives, un-capped, in `agentSkillVersions`.
_MAX_AUDIT_ENTRIES = 50


class AuditGovernor(SkillGovernor):
    def __init__(
        self,
        delegate: SkillGovernor,
        graph_provider: "IGraphDBProvider",
        org_id: str,
        user_id: str,
    ) -> None:
        self._delegate = delegate
        self._graph = graph_provider
        self._org_id = org_id
        self._user_id = user_id

    def _key(self, name: str) -> str:
        return f"{self._org_id}_{name}"

    async def should_approve(self, candidate: SkillCandidate) -> bool:
        return await self._delegate.should_approve(candidate)

    async def get_pending_candidates(self) -> list[SkillCandidate]:
        return await self._delegate.get_pending_candidates()

    async def on_skill_created(self, metadata: SkillMetadata) -> None:
        await self._delegate.on_skill_created(metadata)
        await self._append_audit_entry(metadata.name, action="created", reason=None)

    async def on_skill_deprecated(self, name: str, reason: str) -> None:
        await self._delegate.on_skill_deprecated(name, reason)
        await self._append_audit_entry(name, action="deprecated", reason=reason)

    async def _append_audit_entry(self, name: str, *, action: str, reason: str | None) -> None:
        try:
            doc = await self._graph.get_document(self._key(name), _SKILLS)
            if doc is None or doc.get("orgId") != self._org_id:
                logger.warning("AuditGovernor: skipping audit entry, skill %r not found for org", name)
                return
            actions = [*list(doc.get("auditActions") or []), action][-_MAX_AUDIT_ENTRIES:]
            actor_ids = [*list(doc.get("auditActorIds") or []), self._user_id][-_MAX_AUDIT_ENTRIES:]
            reasons = [*list(doc.get("auditReasons") or []), reason or ""][-_MAX_AUDIT_ENTRIES:]
            timestamps = [*list(doc.get("auditTimestamps") or []), get_epoch_timestamp_in_ms()][-_MAX_AUDIT_ENTRIES:]
            await self._graph.update_node(self._key(name), _SKILLS, {
                "auditActions": actions,
                "auditActorIds": actor_ids,
                "auditReasons": reasons,
                "auditTimestamps": timestamps,
            })
        except Exception:
            logger.exception("AuditGovernor: failed to append audit entry for skill %r action %r", name, action)
