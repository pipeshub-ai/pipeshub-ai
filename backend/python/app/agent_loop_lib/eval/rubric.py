from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from app.agent_loop_lib.core.structured_output import coerce_dict, coerce_list
from app.agent_loop_lib.core.types import UserMessage

if TYPE_CHECKING:
    from app.agent_loop_lib.models.base import SupportsStructuredComplete

"""Rubric-based LLM grading (Phase 5): scores an exported trajectory
(eval/trajectory.py) against a set of weighted criteria via one
structured LLM call — the same `complete_structured` pattern
`ResultCritic`/`PlanCritic` already use, applied post-hoc to a finished
run instead of gating it mid-run. Feeds two consumers: an offline eval
harness (grade a batch of trajectories) and the Phase 5 learning loop
(skill promotion requires a passing grade — see
hooks/middleware/builtin/skill_learning.py).
"""

DEFAULT_PASS_THRESHOLD = 0.7


class RubricCriterion(BaseModel):
    name: str
    description: str
    weight: float = 1.0


class Rubric(BaseModel):
    criteria: list[RubricCriterion]


class CriterionScore(BaseModel):
    name: str
    score: float  # 0.0-1.0
    justification: str = ""


class GradeResult(BaseModel):
    criterion_scores: list[CriterionScore] = Field(default_factory=list)
    overall_score: float = 0.0
    passed: bool = False
    feedback: str = ""


DEFAULT_SKILL_RUBRIC = Rubric(criteria=[
    RubricCriterion(name="task_success", description="The run actually achieved its stated goal.", weight=2.0),
    RubricCriterion(name="efficiency", description="The tool-call pattern is not wasteful or redundant.", weight=1.0),
    RubricCriterion(name="generalizability", description="The pattern would work for other similar goals, not just this one.", weight=1.0),
])


def _grade_schema(rubric: Rubric) -> dict:
    return {
        "type": "object",
        "required": ["criterion_scores", "feedback"],
        "properties": {
            "criterion_scores": {
                "type": "array",
                "description": f"One entry per criterion, in this exact order: {', '.join(c.name for c in rubric.criteria)}",
                "items": {
                    "type": "object",
                    "required": ["name", "score", "justification"],
                    "properties": {
                        "name": {"type": "string"},
                        "score": {"type": "number", "description": "0.0 (fails) to 1.0 (fully meets)"},
                        "justification": {"type": "string"},
                    },
                },
            },
            "feedback": {"type": "string", "description": "One or two sentences of overall feedback"},
        },
    }


def _render_criteria(rubric: Rubric) -> str:
    return "\n".join(f"  - {c.name} (weight {c.weight}): {c.description}" for c in rubric.criteria)


class RubricGrader:
    """LLM-as-judge over a trajectory dict (see eval/trajectory.py's
    `entries_to_trajectory` / `AgentResult`-derived grading input shapes).
    `model` is any `SupportsStructuredComplete` (a `Model`, `LLMTransport`,
    or a test double) — grading is a single structured call, no tool use.
    """

    def __init__(self, model: "SupportsStructuredComplete", pass_threshold: float = DEFAULT_PASS_THRESHOLD) -> None:
        self._model = model
        self._pass_threshold = pass_threshold

    async def grade(self, trajectory: dict, rubric: Rubric | None = None) -> GradeResult:
        rubric = rubric or DEFAULT_SKILL_RUBRIC
        prompt = (
            "Grade the following agent run trajectory against the rubric below. "
            "Score each criterion from 0.0 (completely fails it) to 1.0 (fully meets it).\n\n"
            f"Rubric:\n{_render_criteria(rubric)}\n\n"
            f"Trajectory (JSON):\n{trajectory}\n"
        )
        response = await self._model.complete_structured(
            messages=[UserMessage(content=prompt)],
            output_schema=_grade_schema(rubric),
            system="You are a strict, fair grader for agent run trajectories.",
        )
        raw = response.data if isinstance(response.data, dict) else {}

        weight_by_name = {c.name: c.weight for c in rubric.criteria}
        scores: list[CriterionScore] = []
        for cs in coerce_list(raw.get("criterion_scores", [])):
            cs = coerce_dict(cs)
            if cs is None:
                continue
            try:
                score_value = float(cs.get("score", 0.0))
            except (TypeError, ValueError):
                score_value = 0.0
            scores.append(CriterionScore(
                name=str(cs.get("name", "")),
                score=score_value,
                justification=str(cs.get("justification", "")),
            ))

        total_weight = sum(weight_by_name.get(s.name, 1.0) for s in scores) or 1.0
        overall = sum(s.score * weight_by_name.get(s.name, 1.0) for s in scores) / total_weight

        return GradeResult(
            criterion_scores=scores,
            overall_score=overall,
            passed=overall >= self._pass_threshold,
            feedback=str(raw.get("feedback", "")),
        )
