from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.agent_loop_lib.modules.providers.skills.base import SkillExperience
from app.agent_loop_lib.modules.providers.skills.tracker import SkillUsageTracker


class InMemoryUsageTracker(SkillUsageTracker):
    """Non-persistent default `SkillUsageTracker` — one `SkillExperience`
    per skill name, process-local. Same role as every other `InMemory*`
    provider in this codebase: fast, zero-config, gone on restart."""

    def __init__(self) -> None:
        self._experiences: dict[str, SkillExperience] = {}

    def _get_or_create(self, skill_name: str) -> SkillExperience:
        return self._experiences.setdefault(skill_name, SkillExperience(skill_name=skill_name))

    async def record_activation(self, skill_name: str, session_id: str) -> None:
        experience = self._get_or_create(skill_name)
        experience.total_activations += 1
        experience.last_activated = datetime.now(timezone.utc).isoformat()

    async def record_outcome(self, skill_name: str, session_id: str, success: bool, notes: str = "") -> None:
        experience = self._get_or_create(skill_name)
        if success:
            experience.successful_outcomes += 1
        else:
            experience.failed_outcomes += 1
            if notes:
                experience.failure_modes.append(notes)

    async def get_experience(self, skill_name: str) -> SkillExperience:
        return self._get_or_create(skill_name)

    async def get_underperforming(self, threshold: float = 0.5) -> list[SkillExperience]:
        return [
            e for e in self._experiences.values()
            if (e.successful_outcomes + e.failed_outcomes) > 0 and e.success_rate < threshold
        ]

    async def get_unused(self, since_days: int = 30) -> list[SkillExperience]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
        unused: list[SkillExperience] = []
        for experience in self._experiences.values():
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
