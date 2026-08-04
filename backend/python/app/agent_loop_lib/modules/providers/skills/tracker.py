from __future__ import annotations

from abc import ABC, abstractmethod

from app.agent_loop_lib.modules.providers.skills.base import SkillExperience

"""Usage tracking ABC (MUSE-Autoskill's per-skill memory pattern) — kept
separate from `SkillStore` so tracking can be backed by whatever store the
harness wires up (see `in_memory_tracker.py`) without the skill content
store needing to know anything about activations/outcomes at all.
"""


class SkillUsageTracker(ABC):
    @abstractmethod
    async def record_activation(self, skill_name: str, session_id: str) -> None:
        """Called by `SkillManager.activate_skill` every time a skill's full
        body is loaded (tier-2 progressive disclosure)."""

    @abstractmethod
    async def record_outcome(self, skill_name: str, session_id: str, success: bool, notes: str = "") -> None:
        """Called by the learning-loop middleware once a run that had this
        skill active finishes, closing the loop on whether the skill
        actually helped."""

    @abstractmethod
    async def get_experience(self, skill_name: str) -> SkillExperience:
        """Never raises for an unknown skill — returns a fresh, all-zero
        `SkillExperience` instead, since "no experience yet" is a normal
        state for every newly created skill."""

    @abstractmethod
    async def get_underperforming(self, threshold: float = 0.5) -> list[SkillExperience]:
        """Skills with at least one recorded outcome whose success rate
        falls below `threshold` — candidates for `SkillEvaluator.evaluate_existing`."""

    @abstractmethod
    async def get_unused(self, since_days: int = 30) -> list[SkillExperience]:
        """Skills with no activation in the last `since_days` days (or ever)."""
