from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from app.agent_loop_lib.modules.providers.skills.base import (
    SkillCandidate,
    SkillExperience,
)

if TYPE_CHECKING:
    from app.agent_loop_lib.eval.rubric import Rubric, RubricGrader
    from app.agent_loop_lib.modules.providers.skills.index import SkillIndex

"""Evaluation ABC — the quality gate between "the extractor proposed this"
and "the manager persists it" (see `SkillManager.learn_from_execution`), and
the periodic check that flags an existing skill as underperforming. Kept
separate from `SkillExtractor` (SOLID — Single Responsibility): extraction
proposes, evaluation judges.
"""

logger = logging.getLogger(__name__)

# `SkillExperience.success_rate` below this, with enough samples to trust it,
# is flagged for refinement rather than immediate deprecation.
DEFAULT_UNDERPERFORMING_THRESHOLD = 0.5
DEFAULT_MIN_SAMPLES_FOR_JUDGMENT = 3
# Below this, an underperforming skill is deprecated outright rather than
# just flagged for refinement — it's actively net-negative, not just weak.
DEFAULT_DEPRECATION_THRESHOLD = 0.2


class SkillEvaluator(ABC):
    @abstractmethod
    async def evaluate_candidate(self, candidate: SkillCandidate) -> tuple[bool, str]:
        """Returns (passed, feedback). `passed=False` means the candidate is
        rejected outright — never persisted, not even as a pending
        governance candidate."""

    @abstractmethod
    async def evaluate_existing(self, experience: SkillExperience) -> tuple[str, str]:
        """Returns (action, reason) where action is one of "keep",
        "refine", "deprecate" — used to react to `SkillUsageTracker`'s
        accumulated outcomes for an already-persisted skill."""


class RubricSkillEvaluator(SkillEvaluator):
    """Default `SkillEvaluator`: `RubricGrader` (eval/rubric.py) for
    candidate quality, plus a keyword-search-based dedup check against the
    live index when one is provided."""

    def __init__(
        self,
        grader: "RubricGrader | None" = None,
        rubric: "Rubric | None" = None,
        index: "SkillIndex | None" = None,
        *,
        dedup_relevance_threshold: float = 0.85,
        underperforming_threshold: float = DEFAULT_UNDERPERFORMING_THRESHOLD,
        deprecation_threshold: float = DEFAULT_DEPRECATION_THRESHOLD,
        min_samples_for_judgment: int = DEFAULT_MIN_SAMPLES_FOR_JUDGMENT,
    ) -> None:
        self._grader = grader
        self._rubric = rubric
        self._index = index
        self._dedup_relevance_threshold = dedup_relevance_threshold
        self._underperforming_threshold = underperforming_threshold
        self._deprecation_threshold = deprecation_threshold
        self._min_samples = min_samples_for_judgment

    async def evaluate_candidate(self, candidate: SkillCandidate) -> tuple[bool, str]:
        if not candidate.name or not candidate.description or not candidate.body:
            return False, "candidate is missing a required field (name/description/body)"

        if self._index is not None:
            matches = await self._index.search(candidate.description, limit=3)
            near_duplicate = next(
                (m for m in matches if m.relevance >= self._dedup_relevance_threshold), None,
            )
            if near_duplicate is not None:
                return False, f"too similar to existing skill {near_duplicate.skill.name!r}"

        if self._grader is not None:
            trajectory = {
                "goal": candidate.source_trajectory_summary,
                "name": candidate.name,
                "description": candidate.description,
                "body": candidate.body,
            }
            try:
                grade = await self._grader.grade(trajectory, self._rubric)
            except Exception:
                logger.exception("RubricSkillEvaluator: grading failed for candidate %s", candidate.name)
                return False, "grading failed"
            if not grade.passed:
                return False, f"failed quality grade ({grade.overall_score:.2f}): {grade.feedback}"

        return True, "passed quality gate"

    async def evaluate_existing(self, experience: SkillExperience) -> tuple[str, str]:
        total = experience.successful_outcomes + experience.failed_outcomes
        if total < self._min_samples:
            return "keep", f"only {total} sample(s) so far — not enough to judge"
        if experience.success_rate < self._deprecation_threshold:
            return "deprecate", f"success rate {experience.success_rate:.2f} over {total} runs is net-negative"
        if experience.success_rate < self._underperforming_threshold:
            return "refine", f"success rate {experience.success_rate:.2f} over {total} runs is underperforming"
        return "keep", f"success rate {experience.success_rate:.2f} over {total} runs is healthy"
