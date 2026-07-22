"""`decomposition_scorer.score_plan` (p4-orchestrator-evals) — pure,
offline structural scoring of a `Plan` against a `DecompositionEvalQuery`.
No model calls; every fixture here is a hand-built `Plan`/`PlanStep`."""

from __future__ import annotations

from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.modules.pipeline.planner.base import Plan, PlanStep
from app.agents.agent_loop.evals.decomposition_queries import DecompositionEvalQuery
from app.agents.agent_loop.evals.decomposition_scorer import score_plan

_GOAL = Goal(description="x")


def _query(**overrides: object) -> DecompositionEvalQuery:
    defaults: dict[str, object] = dict(
        id="q1", query="do the thing", min_steps=0, max_steps=1,
        expected_domains=frozenset(), requires_dependency=False,
    )
    defaults.update(overrides)
    return DecompositionEvalQuery(**defaults)  # type: ignore[arg-type]


def _step(**overrides: object) -> PlanStep:
    defaults: dict[str, object] = dict(id="s1", description="do it", domain="calculator")
    defaults.update(overrides)
    return PlanStep(**defaults)  # type: ignore[arg-type]


class TestNoStructuredPlan:
    def test_none_plan_fails(self) -> None:
        score = score_plan(None, _query())
        assert score.passed is False
        assert score.step_count == 0
        assert score.errors

    def test_plan_with_no_steps_fails(self) -> None:
        plan = Plan(goal=_GOAL, text="some free-form text", steps=None)
        score = score_plan(plan, _query())
        assert score.passed is False
        assert score.errors


class TestStepCountRange:
    def test_within_range_passes(self) -> None:
        plan = Plan(goal=_GOAL, text="t", steps=[_step()])
        score = score_plan(plan, _query(min_steps=0, max_steps=1))
        assert score.passed is True
        assert score.step_count == 1

    def test_too_many_steps_fails(self) -> None:
        plan = Plan(goal=_GOAL, text="t", steps=[_step(id="s1"), _step(id="s2")])
        score = score_plan(plan, _query(min_steps=0, max_steps=1))
        assert score.passed is False
        assert any("step count" in i.message for i in score.errors)

    def test_too_few_steps_fails(self) -> None:
        plan = Plan(goal=_GOAL, text="t", steps=[_step()])
        score = score_plan(plan, _query(min_steps=2, max_steps=3))
        assert score.passed is False


class TestExpectedDomains:
    def test_matching_domain_passes(self) -> None:
        plan = Plan(goal=_GOAL, text="t", steps=[_step(domain="calculator_agent")])
        score = score_plan(plan, _query(min_steps=0, max_steps=1, expected_domains=frozenset({"calculator"})))
        assert score.passed is True

    def test_missing_domain_fails(self) -> None:
        plan = Plan(goal=_GOAL, text="t", steps=[_step(domain="calendar_agent")])
        score = score_plan(plan, _query(min_steps=0, max_steps=1, expected_domains=frozenset({"calculator"})))
        assert score.passed is False
        assert any("calculator" in i.message for i in score.errors)

    def test_domain_evidenced_via_tool_names_not_just_domain_field(self) -> None:
        plan = Plan(goal=_GOAL, text="t", steps=[
            _step(domain="misc", tool_names=["jira_search_issues"]),
        ])
        score = score_plan(plan, _query(min_steps=0, max_steps=1, expected_domains=frozenset({"internal"})))
        assert score.passed is True

    def test_domain_evidenced_via_description(self) -> None:
        plan = Plan(goal=_GOAL, text="t", steps=[
            _step(domain="misc", description="generate a CSV file of the results"),
        ])
        score = score_plan(plan, _query(min_steps=0, max_steps=1, expected_domains=frozenset({"coding"})))
        assert score.passed is True

    def test_multiple_expected_domains_all_required(self) -> None:
        plan = Plan(goal=_GOAL, text="t", steps=[
            _step(id="s1", domain="web_agent"),
            _step(id="s2", domain="coding_agent"),
        ])
        query = _query(min_steps=2, max_steps=2, expected_domains=frozenset({"web", "coding", "calendar"}))
        score = score_plan(plan, query)
        assert score.passed is False
        assert any("calendar" in i.message for i in score.errors)


class TestDependencyRequirement:
    def test_required_dependency_present_passes(self) -> None:
        plan = Plan(goal=_GOAL, text="t", steps=[
            _step(id="fetch", domain="internal_exploration_agent"),
            _step(id="calc", domain="calculator_agent", depends_on=["fetch"]),
        ])
        query = _query(min_steps=2, max_steps=2, requires_dependency=True)
        score = score_plan(plan, query)
        assert score.passed is True

    def test_required_dependency_missing_fails(self) -> None:
        plan = Plan(goal=_GOAL, text="t", steps=[
            _step(id="fetch", domain="internal_exploration_agent"),
            _step(id="calc", domain="calculator_agent"),
        ])
        query = _query(min_steps=2, max_steps=2, requires_dependency=True)
        score = score_plan(plan, query)
        assert score.passed is False
        assert any("depends_on" in i.message for i in score.errors)

    def test_dependency_not_required_is_fine_either_way(self) -> None:
        plan = Plan(goal=_GOAL, text="t", steps=[_step(id="a"), _step(id="b")])
        query = _query(min_steps=2, max_steps=2, requires_dependency=False)
        score = score_plan(plan, query)
        assert score.passed is True


class TestBoundariesWarning:
    def test_single_step_plan_needs_no_boundaries(self) -> None:
        plan = Plan(goal=_GOAL, text="t", steps=[_step(boundaries=[])])
        score = score_plan(plan, _query(min_steps=0, max_steps=1))
        assert score.passed is True
        assert not score.warnings

    def test_multi_step_plan_missing_boundaries_warns_but_does_not_fail(self) -> None:
        plan = Plan(goal=_GOAL, text="t", steps=[
            _step(id="a", boundaries=[]), _step(id="b", boundaries=[]),
        ])
        score = score_plan(plan, _query(min_steps=2, max_steps=2))
        assert score.passed is True
        assert score.warnings
        assert any("boundaries" in w.message for w in score.warnings)

    def test_multi_step_plan_with_boundaries_has_no_warning(self) -> None:
        plan = Plan(goal=_GOAL, text="t", steps=[
            _step(id="a", boundaries=["do not cover b's scope"]),
            _step(id="b", boundaries=["do not cover a's scope"]),
        ])
        score = score_plan(plan, _query(min_steps=2, max_steps=2))
        assert not score.warnings
