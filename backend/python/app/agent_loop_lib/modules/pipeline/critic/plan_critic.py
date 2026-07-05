from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent_loop_lib.core.types import UserMessage
from app.agent_loop_lib.modules.pipeline.planner.base import parse_confidence
from app.agent_loop_lib.modules.pipeline.critic.base import (
    Critic,
    CritiqueIssue,
    CritiqueResult,
)
from app.agent_loop_lib.modules.pipeline.planner.base import Plan

if TYPE_CHECKING:
    from app.agent_loop_lib.models.base import SupportsStructuredComplete


_SCHEMA = {
    "type": "object",
    "properties": {
        "passed": {"type": "boolean"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "summary": {"type": "string"},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["error", "warning", "suggestion"]},
                    "description": {"type": "string"},
                    "location": {"type": "string"},
                },
                "required": ["severity", "description"],
            },
        },
    },
    "required": ["passed", "confidence", "summary", "issues"],
}


class PlanCritic(Critic):
    """Evaluates a Plan for feasibility and completeness before execution."""

    def __init__(self, model: "SupportsStructuredComplete | None" = None) -> None:
        self._model = model

    async def critique(self, subject: Plan) -> CritiqueResult:
        if self._model is None:
            passed = len(subject.phases) > 0
            return CritiqueResult(
                passed=passed,
                confidence=Confidence.LOW,
                issues=[],
                summary="Plan has phases" if passed else "Plan has no phases — nothing to execute",
            )

        phase_lines = "\n".join(
            f"  {i+1}. {p.name}: {p.description}"
            for i, p in enumerate(subject.phases)
        )
        prompt = (
            f"Evaluate the following execution plan for the goal: {subject.goal.description!r}\n\n"
            f"Phases:\n{phase_lines or '  (none)'}\n\n"
            "Assess feasibility, completeness, and correctness. "
            "Return 'passed: true' if the plan is sound enough to execute."
        )

        response = await self._model.complete_structured(
            messages=[UserMessage(content=prompt)],
            output_schema=_SCHEMA,
        )
        raw = response.data

        issues = [
            CritiqueIssue(
                severity=i.get("severity", "warning"),
                description=i.get("description", ""),
                location=i.get("location"),
            )
            for i in raw.get("issues", [])
        ]
        return CritiqueResult(
            passed=raw.get("passed", True),
            confidence=parse_confidence(raw.get("confidence", "low")),
            issues=issues,
            summary=raw.get("summary", ""),
        )
