"""Deterministic, offline scoring of an `IntentRouteDecision` against an
`AskActEvalQuery` — no model call, no network. Mirrors
`decomposition_scorer.py`'s shape (a pure function + a small dataclass
report) for the ask-vs-act half of the eval suite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agents.agent_loop.evals.ask_act_queries import AskActEvalQuery
    from app.agents.agent_loop.intent import IntentRouteDecision


@dataclass
class AskActScore:
    query_id: str
    passed: bool
    asked: bool
    expected_ask: bool
    severity: str | None = None
    message: str = ""


def score_decision(decision: "IntentRouteDecision", query: "AskActEvalQuery") -> AskActScore:
    """`asked` is simply "did the model take the ```clarify escape hatch"
    (`decision.clarifying_questions` non-empty) — the binary this eval
    exists to check. `passed` is that binary matching `query.expect_ask`;
    everything else (question count, severity, option quality) is
    observability, not a pass/fail criterion here."""
    asked = bool(decision.clarifying_questions)
    passed = asked == query.expect_ask
    if passed:
        message = f"asked={asked} matches expected={query.expect_ask}."
    elif query.expect_ask:
        message = "expected the model to ask a clarifying question, but it answered/acted instead."
    else:
        message = "expected the model to act directly, but it asked a clarifying question instead."
    return AskActScore(
        query_id=query.id,
        passed=passed,
        asked=asked,
        expected_ask=query.expect_ask,
        severity=decision.clarification_severity,
        message=message,
    )


@dataclass
class AskActEvalReport:
    scores: list[AskActScore] = field(default_factory=list)

    @property
    def pass_count(self) -> int:
        return sum(1 for s in self.scores if s.passed)

    @property
    def fail_count(self) -> int:
        return len(self.scores) - self.pass_count

    @property
    def pass_rate(self) -> float:
        return self.pass_count / len(self.scores) if self.scores else 0.0

    def render_text(self) -> str:
        lines = [f"Ask-vs-act eval: {self.pass_count}/{len(self.scores)} passed ({self.pass_rate:.0%})", ""]
        for score in self.scores:
            status = "PASS" if score.passed else "FAIL"
            lines.append(f"[{status}] {score.query_id} (asked={score.asked}, severity={score.severity}) — {score.message}")
        return "\n".join(lines)


__all__ = ["AskActEvalReport", "AskActScore", "score_decision"]
