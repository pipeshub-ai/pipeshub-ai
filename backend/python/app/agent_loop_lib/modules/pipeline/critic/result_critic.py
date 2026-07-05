from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent_loop_lib.core.types import AgentResult, UserMessage
from app.agent_loop_lib.modules.pipeline.planner.base import parse_confidence
from app.agent_loop_lib.modules.pipeline.critic.base import (
    Critic,
    CritiqueIssue,
    CritiqueResult,
)

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


class ResultCritic(Critic):
    """Verifies an AgentResult against the original Goal's success criteria."""

    def __init__(self, model: "SupportsStructuredComplete | None" = None) -> None:
        self._model = model

    async def critique(self, subject: AgentResult) -> CritiqueResult:
        if self._model is None:
            return CritiqueResult(
                passed=subject.success,
                confidence=Confidence.LOW,
                issues=[],
                summary="Result marked successful" if subject.success else f"Result failed: {subject.error}",
            )

        success_criteria = "\n".join(
            f"  - {c}" for c in (subject.goal.success_criteria or [])
        )
        prompt = (
            f"Evaluate whether the agent result meets the goal.\n\n"
            f"Goal: {subject.goal.description!r}\n"
            f"Success criteria:\n{success_criteria or '  (none specified)'}\n\n"
            f"Agent output: {str(subject.output)[:1000]}\n"
            f"Success flag: {subject.success}\n"
            f"Error: {subject.error or 'none'}\n\n"
            "Assess whether the result genuinely satisfies the goal and criteria."
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
