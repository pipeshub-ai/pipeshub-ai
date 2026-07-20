"""`GraphUsageTracker`: agent_loop_lib's `SkillUsageTracker` persisted onto
the same `agentSkills` document `GraphSkillStore` owns (the `usage*`
fields — see `app/schema/arango/documents.py::agent_skills_schema`), via
plain `get_document`/`update_node` read-modify-write. This is the MUSE-
Autoskill per-skill memory the in-memory `InMemoryUsageTracker` describes,
made durable across restarts/instances so `SkillManager.evaluate_skill_health`
reflects the org's real, accumulated usage rather than resetting on every
process start.

Counters tolerate rare lost increments under concurrent writes to the SAME
skill (a read-modify-write, not an atomic `$inc`) — acceptable for a
per-skill activation/outcome counter at the concurrency this subsystem
sees (interactive agent runs, not a hot request-counting path); an exact
count was never a correctness requirement here, only a directional
"heavily used"/"rarely fails" signal for the evaluator.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.modules.providers.skills.base import SkillExperience
from app.agent_loop_lib.modules.providers.skills.tracker import SkillUsageTracker
from app.config.constants.arangodb import CollectionNames
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = ["GraphUsageTracker"]

logger = logging.getLogger(__name__)

_SKILLS = CollectionNames.AGENT_SKILLS.value

# Bounds how many recent failure/improvement notes accumulate per skill —
# these exist to give a human/governor a taste of WHY a skill is
# underperforming, not to be a full audit log (that's `agentSkillVersions`).
_MAX_NOTES = 20


def _doc_to_experience(name: str, doc: dict | None) -> SkillExperience:
    if doc is None:
        return SkillExperience(skill_name=name)
    return SkillExperience(
        skill_name=name,
        total_activations=int(doc.get("usageTotalActivations") or 0),
        successful_outcomes=int(doc.get("usageSuccessfulOutcomes") or 0),
        failed_outcomes=int(doc.get("usageFailedOutcomes") or 0),
        last_activated=doc.get("usageLastActivated"),
        failure_modes=list(doc.get("usageFailureModes") or []),
        improvement_notes=list(doc.get("usageImprovementNotes") or []),
    )


class GraphUsageTracker(SkillUsageTracker):
    def __init__(self, graph_provider: "IGraphDBProvider", org_id: str, user_id: str) -> None:
        self._graph = graph_provider
        self._org_id = org_id
        self._user_id = user_id

    def _key(self, name: str) -> str:
        return f"{self._org_id}_{name}"

    async def _get_org_doc(self, name: str) -> dict | None:
        doc = await self._graph.get_document(self._key(name), _SKILLS)
        if doc is None or doc.get("orgId") != self._org_id:
            return None
        return doc

    async def record_activation(self, skill_name: str, session_id: str) -> None:
        doc = await self._get_org_doc(skill_name)
        if doc is None:
            logger.debug("GraphUsageTracker: skipping activation for unknown skill %r", skill_name)
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        await self._graph.update_node(self._key(skill_name), _SKILLS, {
            "usageTotalActivations": int(doc.get("usageTotalActivations") or 0) + 1,
            "usageLastActivated": now_iso,
            "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
        })

    async def record_outcome(self, skill_name: str, session_id: str, success: bool, notes: str = "") -> None:
        doc = await self._get_org_doc(skill_name)
        if doc is None:
            logger.debug("GraphUsageTracker: skipping outcome for unknown skill %r", skill_name)
            return
        updates: dict[str, Any] = {"updatedAtTimestamp": get_epoch_timestamp_in_ms()}
        if success:
            updates["usageSuccessfulOutcomes"] = int(doc.get("usageSuccessfulOutcomes") or 0) + 1
        else:
            updates["usageFailedOutcomes"] = int(doc.get("usageFailedOutcomes") or 0) + 1
            if notes:
                failure_modes = list(doc.get("usageFailureModes") or [])
                failure_modes.append(notes)
                updates["usageFailureModes"] = failure_modes[-_MAX_NOTES:]
        await self._graph.update_node(self._key(skill_name), _SKILLS, updates)

    async def get_experience(self, skill_name: str) -> SkillExperience:
        doc = await self._get_org_doc(skill_name)
        return _doc_to_experience(skill_name, doc)

    async def get_underperforming(self, threshold: float = 0.5) -> list[SkillExperience]:
        docs = await self._graph.get_nodes_by_filters(_SKILLS, {"orgId": self._org_id})
        underperforming: list[SkillExperience] = []
        for doc in docs:
            experience = _doc_to_experience(doc.get("name", ""), doc)
            total = experience.successful_outcomes + experience.failed_outcomes
            if total > 0 and experience.success_rate < threshold:
                underperforming.append(experience)
        return underperforming

    async def get_unused(self, since_days: int = 30) -> list[SkillExperience]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
        docs = await self._graph.get_nodes_by_filters(_SKILLS, {"orgId": self._org_id})
        unused: list[SkillExperience] = []
        for doc in docs:
            experience = _doc_to_experience(doc.get("name", ""), doc)
            if experience.last_activated is None:
                unused.append(experience)
                continue
            try:
                last = datetime.fromisoformat(experience.last_activated)
            except ValueError:
                continue
            if last < cutoff:
                unused.append(experience)
        return unused
