"""`PlanCritic` and `ResultCritic`, the reviewers behind `critique_plan` and
`verify_result` in the `planExecute` chat mode: what each one is shown, how
its verdict is read, and what it decides when no model is available."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agent_loop_lib.core.types import AgentResult, Confidence, Goal
from app.agent_loop_lib.modules.pipeline.critic.plan_critic import PlanCritic
from app.agent_loop_lib.modules.pipeline.critic.result_critic import ResultCritic
from app.agent_loop_lib.modules.pipeline.planner.base import Plan
from tests.unit.agents.adapter.support.verdict_transport import VerdictTransport

_GOAL = Goal(description="Summarise this week's open support tickets")


class TestPlanCriticWithoutAModel:
    async def test_an_empty_plan_is_rejected(self) -> None:
        verdict = await PlanCritic().critique(Plan(goal=_GOAL, text="   \n"))

        assert verdict.passed is False
        assert verdict.summary == "Plan is empty — nothing to execute"
        assert verdict.confidence == Confidence.LOW

    async def test_a_plan_with_content_passes(self) -> None:
        verdict = await PlanCritic().critique(Plan(goal=_GOAL, text="1. fetch tickets"))

        assert verdict.passed is True
        assert verdict.summary == "Plan has content"


class TestPlanCriticWithAModel:
    async def test_the_reviewer_sees_the_goal_and_the_plan(self) -> None:
        model = VerdictTransport([{"passed": True, "confidence": "high", "summary": "ok", "issues": []}])

        await PlanCritic(model).critique(Plan(goal=_GOAL, text="1. fetch tickets\n2. summarise"))

        assert _GOAL.description in model.structured_prompts[0]
        assert "1. fetch tickets\n2. summarise" in model.structured_prompts[0]

    async def test_an_empty_plan_is_shown_to_the_reviewer_as_none(self) -> None:
        model = VerdictTransport()

        await PlanCritic(model).critique(Plan(goal=_GOAL, text=""))

        assert "Plan:\n  (none)" in model.structured_prompts[0]

    async def test_issues_and_confidence_are_read_leniently(self) -> None:
        model = VerdictTransport([{
            "passed": False, "confidence": "High", "summary": "gaps",
            "issues": [{"description": "No step covers the deadline"}],
        }])

        verdict = await PlanCritic(model).critique(Plan(goal=_GOAL, text="1. fetch"))

        assert verdict.passed is False
        assert verdict.confidence == Confidence.HIGH
        assert verdict.summary == "gaps"
        assert len(verdict.issues) == 1
        assert verdict.issues[0].severity == "warning"
        assert verdict.issues[0].description == "No step covers the deadline"
        assert verdict.issues[0].location is None

    async def test_a_verdict_without_a_decision_counts_as_a_pass(self) -> None:
        verdict = await PlanCritic(VerdictTransport([{}])).critique(Plan(goal=_GOAL, text="1. fetch"))

        assert verdict.passed is True
        assert verdict.confidence == Confidence.LOW
        assert verdict.issues == []


def _answer(output: str, *, success: bool = True, error: str | None = None, goal: Goal = _GOAL) -> AgentResult:
    return AgentResult(goal=goal, output=output, turns=[], success=success, error=error)


class TestResultCriticWithoutAModel:
    async def test_a_successful_result_passes(self) -> None:
        verdict = await ResultCritic().critique(_answer("the summary"))

        assert verdict.passed is True
        assert verdict.summary == "Result marked successful"
        assert verdict.confidence == Confidence.LOW

    async def test_a_failed_result_fails_with_its_error(self) -> None:
        verdict = await ResultCritic().critique(_answer("", success=False, error="search timed out"))

        assert verdict.passed is False
        assert verdict.summary == "Result failed: search timed out"


class TestResultCriticWithAModel:
    async def test_the_checker_sees_the_goal_its_criteria_and_the_answer(self) -> None:
        goal = _GOAL.model_copy(update={"success_criteria": ["groups tickets by customer", "gives totals"]})
        model = VerdictTransport([{"passed": True, "confidence": "medium", "summary": "ok", "issues": []}])

        verdict = await ResultCritic(model).critique(_answer("Acme: 3 tickets", goal=goal))

        prompt = model.structured_prompts[0]
        assert _GOAL.description in prompt
        assert "  - groups tickets by customer\n  - gives totals" in prompt
        assert "Agent output: Acme: 3 tickets" in prompt
        assert "Error: none" in prompt
        assert verdict.passed is True
        assert verdict.confidence == Confidence.MEDIUM

    async def test_a_goal_without_criteria_says_so(self) -> None:
        model = VerdictTransport()

        await ResultCritic(model).critique(_answer("Acme: 3 tickets"))

        assert "Success criteria:\n  (none specified)" in model.structured_prompts[0]

    async def test_a_weak_answer_verdict_carries_its_issues(self) -> None:
        model = VerdictTransport([{
            "passed": False, "confidence": "low", "summary": "incomplete",
            "issues": [{"severity": "error", "description": "misses the totals", "location": "last paragraph"}],
        }])

        verdict = await ResultCritic(model).critique(_answer("Acme: 3 tickets"))

        assert verdict.passed is False
        assert verdict.issues[0].severity == "error"
        assert verdict.issues[0].location == "last paragraph"


class TestScriptedVerdicts:
    def test_a_misspelled_field_is_refused_instead_of_reading_as_a_pass(self) -> None:
        with pytest.raises(ValidationError, match="pasdsed"):
            VerdictTransport([{"pasdsed": False}])

    def test_a_misspelled_issue_field_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="descripton"):
            VerdictTransport([{"passed": False, "issues": [{"descripton": "gap"}]}])

    @pytest.mark.parametrize("field", ["issues", "passed", "summary"])
    def test_an_explicit_null_is_refused_since_the_critics_cannot_read_it(self, field: str) -> None:
        with pytest.raises(ValidationError, match=field):
            VerdictTransport([{"passed": False, field: None}])

    async def test_partial_and_empty_verdicts_are_still_accepted(self) -> None:
        model = VerdictTransport([{"passed": False}, {}])
        critic = PlanCritic(model)

        assert (await critic.critique(Plan(goal=_GOAL, text="1. fetch"))).passed is False
        assert (await critic.critique(Plan(goal=_GOAL, text="1. fetch"))).passed is True
