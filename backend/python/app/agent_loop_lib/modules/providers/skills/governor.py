from __future__ import annotations

from abc import ABC, abstractmethod

from app.agent_loop_lib.modules.providers.skills.base import (
    SkillCandidate,
    SkillMetadata,
)

"""Governance extension point (SOLID — Open/Closed): today's `SkillManager`
calls this ABC at every mutation-worthy point, but ships only trivial
policy implementations. A future governance engine (human approval
workflows, audit logging, conflict detection, skill promotion/pruning —
see the plan's "Future: Skill Governance Engine" section) plugs in here
without any change to `SkillManager` itself.
"""


class SkillGovernor(ABC):
    @abstractmethod
    async def should_approve(self, candidate: SkillCandidate) -> bool:
        """Policy decision: may this candidate be persisted as a real skill
        right now, without further human review?"""

    @abstractmethod
    async def on_skill_created(self, metadata: SkillMetadata) -> None:
        """Notification hook — e.g. for an audit log. No return value."""

    @abstractmethod
    async def on_skill_deprecated(self, name: str, reason: str) -> None:
        ...

    @abstractmethod
    async def get_pending_candidates(self) -> list[SkillCandidate]:
        """A governor with its own independent approval queue (e.g. a
        database-backed workflow engine) returns it here. Default
        implementations return an empty list — `SkillManager` maintains its
        own pending-candidate bookkeeping via the store's `_meta/candidates/`
        directory regardless of which governor is wired."""


class AutoApproveGovernor(SkillGovernor):
    """Always approves — the default when `auto_approve=True` and no
    custom governor is supplied. Notifications are no-ops."""

    async def should_approve(self, candidate: SkillCandidate) -> bool:
        return True

    async def on_skill_created(self, metadata: SkillMetadata) -> None:
        return None

    async def on_skill_deprecated(self, name: str, reason: str) -> None:
        return None

    async def get_pending_candidates(self) -> list[SkillCandidate]:
        return []


class ManualReviewGovernor(SkillGovernor):
    """Never auto-approves — every candidate is queued for human review
    (the default when `write_approval=True`). Notifications are no-ops;
    a real governance engine overrides them to maintain an audit log."""

    async def should_approve(self, candidate: SkillCandidate) -> bool:
        return False

    async def on_skill_created(self, metadata: SkillMetadata) -> None:
        return None

    async def on_skill_deprecated(self, name: str, reason: str) -> None:
        return None

    async def get_pending_candidates(self) -> list[SkillCandidate]:
        return []
