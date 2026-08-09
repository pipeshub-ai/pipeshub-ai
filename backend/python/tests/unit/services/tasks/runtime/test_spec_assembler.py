"""Unit tests for the pure parts of `TaskSpecAssembler` -- `build_goal` and
`build_agent_spec` do no I/O, so they're tested directly without a graph
provider, config service, or tool registry. The I/O-driving methods
(`assemble`, `build_context_and_tools`, `_resolve_llm`) are exercised by
the headless-execution integration test instead
(`tests/integration/services/tasks/test_headless_execution.py`), which
needs a real `Agent`/`AgentRuntime` anyway to prove the checkpoint/resume
contract.
"""
from __future__ import annotations

import logging

from app.agent_loop_lib.agent.loops import PlanCritiqueExecuteLoop, ReActLoop
from app.agent_loop_lib.core.types import AgentResult, Goal
from app.services.tasks.domain.models import TaskDefinition, TaskPrincipal, TaskStep
from app.services.tasks.domain.policies import BudgetPolicy
from app.services.tasks.runtime.spec_assembler import (
    TaskDagLoop,
    TaskSpecAssembler,
    compute_step_report,
)


def _make_task(**overrides) -> TaskDefinition:
    defaults = {
        "org_id": "org-1",
        "created_by_user_id": "user-1",
        "principal": TaskPrincipal(org_id="org-1", user_id="user-1", user_email="a@b.com"),
        "title": "Daily digest",
        "description": "every morning summarize tickets",
        "instructions": "Summarize yesterday's tickets and post to #support",
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
    }
    defaults.update(overrides)
    return TaskDefinition(**defaults)


class TestBuildGoal:
    def test_uses_instructions_not_description(self) -> None:
        task = _make_task(description="raw ask", instructions="assembled prompt")
        goal = TaskSpecAssembler.build_goal(task)
        assert goal.description == "assembled prompt"

    def test_folds_clarifications_into_goal(self) -> None:
        task = _make_task(clarifications=[{"question": "which channel?", "answer": "#support"}])
        goal = TaskSpecAssembler.build_goal(task)
        assert goal.clarifications == {"which channel?": "#support"}

    def test_no_clarifications_yields_empty_dict(self) -> None:
        task = _make_task()
        goal = TaskSpecAssembler.build_goal(task)
        assert goal.clarifications == {}


class TestBuildAgentSpec:
    def test_spec_name_is_scoped_to_task_id(self) -> None:
        task = _make_task()
        spec = TaskSpecAssembler.build_agent_spec(task, tool_names=[], model_name="claude-sonnet-4-6")
        assert spec.name == f"task:{task.task_id}"

    def test_tool_names_pass_through(self) -> None:
        task = _make_task()
        spec = TaskSpecAssembler.build_agent_spec(task, tool_names=["search", "slack_post"], model_name="m")
        assert spec.tool_names == ["search", "slack_post"]

    def test_max_turns_takes_the_stricter_of_the_two_caps(self) -> None:
        task = _make_task(max_turns=20, budget=BudgetPolicy(max_turns_per_run=5))
        spec = TaskSpecAssembler.build_agent_spec(task, tool_names=[], model_name="m")
        assert spec.max_turns == 5

        task2 = _make_task(max_turns=3, budget=BudgetPolicy(max_turns_per_run=15))
        spec2 = TaskSpecAssembler.build_agent_spec(task2, tool_names=[], model_name="m")
        assert spec2.max_turns == 3

    def test_loop_strategy_resolved_by_name(self) -> None:
        task = _make_task(loop_strategy_name="plan_critique_execute")
        spec = TaskSpecAssembler.build_agent_spec(task, tool_names=[], model_name="m")
        assert isinstance(spec.loop, PlanCritiqueExecuteLoop)

    def test_unknown_loop_strategy_falls_back_to_react(self) -> None:
        task = _make_task(loop_strategy_name="not-a-real-loop")
        spec = TaskSpecAssembler.build_agent_spec(task, tool_names=[], model_name="m")
        assert isinstance(spec.loop, ReActLoop)

    def test_model_provider_is_always_langchain(self) -> None:
        task = _make_task()
        spec = TaskSpecAssembler.build_agent_spec(task, tool_names=[], model_name="claude-sonnet-4-6")
        assert spec.model.provider == "langchain"
        assert spec.model.model == "claude-sonnet-4-6"

    def test_task_with_steps_always_uses_task_dag_loop(self) -> None:
        """Phase 6: a structured `steps` DAG overrides `loop_strategy_name`
        entirely -- there is no model-driven decision left to make about
        the run's shape once the steps and their dependencies were fixed
        at creation time."""
        task = _make_task(
            loop_strategy_name="plan_critique_execute",
            steps=[TaskStep(id="fetch", description="fetch data", domain="jira")],
        )
        spec = TaskSpecAssembler.build_agent_spec(task, tool_names=[], model_name="m")
        assert isinstance(spec.loop, TaskDagLoop)

    def test_task_without_steps_is_unaffected(self) -> None:
        task = _make_task(loop_strategy_name="react")
        spec = TaskSpecAssembler.build_agent_spec(task, tool_names=[], model_name="m")
        assert isinstance(spec.loop, ReActLoop)
        assert not isinstance(spec.loop, TaskDagLoop)


class _FakeConfigService:
    """Minimal `ConfigurationService` stand-in: `/services/toolset-instances`
    returns the admin-created instance list (`instanceName`/`toolsetType`
    only -- see `toolsets.py::create_toolset_instance`), everything else is
    an authenticated per-user credential keyed by whatever path was asked
    for."""

    def __init__(self, instances: list[dict], *, authenticated: bool = True) -> None:
        self._instances = instances
        self._authenticated = authenticated

    async def get_config(self, path: str, default=None):
        if path == "/services/toolset-instances":
            return self._instances
        return {"isAuthenticated": self._authenticated}


class TestResolveToolsetCredentials:
    """The admin-created instance metadata at `/services/toolset-instances`
    only ever has `instanceName`/`toolsetType` (`toolsets.py
    ::create_toolset_instance`'s `new_instance` dict) -- never `name`,
    `displayName`, `tools`, or `selectedTools`. `PipesHubToolLoader
    ._build_configured_apps_set` and `ToolInstanceCreator._get_config_for_app`
    both key off the `agent_toolsets` entry's `name`, so a headless run must
    populate it from `toolsetType`, not from a field that never exists."""

    async def test_name_and_type_come_from_toolset_type_not_a_missing_field(self) -> None:
        task = _make_task(toolset_ids=["inst-1"])
        config_service = _FakeConfigService([
            {"_id": "inst-1", "instanceName": "My Calendar", "toolsetType": "calendar"},
        ])
        agent_toolsets, toolset_configs = await TaskSpecAssembler()._resolve_toolset_credentials(
            task, config_service=config_service, log=logging.getLogger(__name__),
        )
        assert agent_toolsets == [{
            "instanceId": "inst-1",
            "instanceName": "My Calendar",
            "name": "calendar",
            "displayName": "My Calendar",
            "type": "calendar",
            "tools": [],
            "selectedTools": [],
        }]
        assert "inst-1" in toolset_configs

    async def test_unauthenticated_instance_is_dropped(self) -> None:
        task = _make_task(toolset_ids=["inst-1"])
        config_service = _FakeConfigService(
            [{"_id": "inst-1", "instanceName": "My Calendar", "toolsetType": "calendar"}],
            authenticated=False,
        )
        agent_toolsets, toolset_configs = await TaskSpecAssembler()._resolve_toolset_credentials(
            task, config_service=config_service, log=logging.getLogger(__name__),
        )
        assert agent_toolsets == []
        assert toolset_configs == {}

    async def test_no_toolset_ids_is_a_noop(self) -> None:
        task = _make_task()
        agent_toolsets, toolset_configs = await TaskSpecAssembler()._resolve_toolset_credentials(
            task, config_service=_FakeConfigService([]), log=logging.getLogger(__name__),
        )
        assert agent_toolsets == []
        assert toolset_configs == {}


def _result(*, success: bool, output: str = "", needs_input: str | None = None) -> AgentResult:
    return AgentResult(goal=Goal(description="x"), output=output, success=success, needs_input=needs_input)


class TestComputeStepReport:
    """Pure classification logic (task engine plan Phase 6 "skipped-
    dependent reporting") -- no I/O, no `Agent`, no `schedule_spawn_batch`.
    `TaskDagLoop`'s own end-to-end behavior is covered separately in
    `tests/integration/services/tasks/` alongside the rest of the
    headless-execution contract."""

    def test_all_steps_succeed(self) -> None:
        steps = [TaskStep(id="a", description="d"), TaskStep(id="b", description="d", depends_on=["a"])]
        results = {"a": _result(success=True, output="A done"), "b": _result(success=True, output="B done")}
        report = compute_step_report(steps, results)
        assert report.completed_steps == ["a", "b"]
        assert report.failed_step_id is None
        assert report.skipped_steps == []
        assert report.is_clean is True

    def test_failed_step_marks_transitive_dependents_as_skipped_not_failed(self) -> None:
        steps = [
            TaskStep(id="fetch", description="d"),
            TaskStep(id="build", description="d", depends_on=["fetch"]),
            TaskStep(id="publish", description="d", depends_on=["build"]),
        ]
        # "build" and "publish" never actually ran (schedule_spawn_batch
        # never launches a child whose prerequisite failed), so neither has
        # an entry in `results` at all -- only "fetch" does.
        results = {"fetch": _result(success=False)}
        report = compute_step_report(steps, results)
        assert report.completed_steps == []
        assert report.failed_step_id == "fetch"
        assert report.skipped_steps == ["build", "publish"]

    def test_independent_branch_succeeds_despite_sibling_failure(self) -> None:
        steps = [
            TaskStep(id="a", description="d"),
            TaskStep(id="b", description="d", depends_on=["a"]),
            TaskStep(id="c", description="d"),  # independent of a/b
        ]
        results = {"a": _result(success=False), "c": _result(success=True, output="C done")}
        report = compute_step_report(steps, results)
        assert report.completed_steps == ["c"]
        assert report.failed_step_id == "a"
        assert report.skipped_steps == ["b"]

    def test_two_independent_failures_both_reported(self) -> None:
        steps = [TaskStep(id="a", description="d"), TaskStep(id="b", description="d")]
        results = {"a": _result(success=False), "b": _result(success=False)}
        report = compute_step_report(steps, results)
        assert report.failed_step_id == "a"
        assert report.additional_failed_step_ids == ["b"]
        assert set(report.all_failed_step_ids) == {"a", "b"}

    def test_step_missing_from_results_entirely_is_treated_as_failed(self) -> None:
        """E.g. `validate_spawn_batch` rejected it (bad tool name, fan-out
        cap) before it ever ran -- never silently dropped from the report."""
        steps = [TaskStep(id="a", description="d")]
        report = compute_step_report(steps, results={})
        assert report.failed_step_id == "a"
        assert report.completed_steps == []
