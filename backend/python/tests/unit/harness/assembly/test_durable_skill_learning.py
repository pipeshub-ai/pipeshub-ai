"""DUR2-S1, S3..S5: today's skill learning runs on a durable run as its post-run step (DUR1 open item 5)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.hooks.middleware.builtin.skill_learning import SkillLearning
from app.agents.agent_loop.skills_wiring import skill_writer
from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.assembly.skill_learning import (
    SKILL_LEARNING_STEP,
    SkillLearningStep,
    agent_result,
)
from app.services.featureflag.platform_settings import PLATFORM_SETTINGS_KEY
from harness_core.kernel.durable.ports import RunEnding
from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.durable import ScriptedModelPort
from tests.support.config_service import RIG_PLATFORM_FLAGS
from tests.unit.agent_loop_lib.hooks.middleware.builtin.test_skill_learning import (
    _candidate,
    _FakeSkillManager,
)
from tests.unit.harness.assembly import world as w

if TYPE_CHECKING:
    from app.agent_loop_lib.core.types import AgentResult

LOAD = ToolCall(id="c1", name="load_skill", arguments={"name": "deploy"})
SEARCH = ToolCall(id="c2", name="search_issues", arguments={"jql": "x"})


def _ending(outcome: str = "answered", **overrides: Any) -> RunEnding:  # noqa: ANN401
    return RunEnding(outcome, ("thinking", "done: deployed"), tool_calls=(LOAD, SEARCH), **overrides)  # type: ignore[arg-type]


def _step(manager: _FakeSkillManager, spawned: list[str], session_id: str | None = None) -> SkillLearningStep:
    async def spawn(goal: str) -> AgentResult:
        spawned.append(goal)
        return agent_result(_ending(), goal)

    return SkillLearningStep(
        SkillLearning(manager, spawn),  # type: ignore[arg-type]
        goal="deploy the service",
        run_id="run-1",
        session_id=session_id,
    )


def test_dur2_s1_the_runs_ending_is_what_legacy_hands_post_agent() -> None:
    result = agent_result(_ending(answer="the answer"), "deploy the service")
    assert result.goal.description == "deploy the service"
    assert result.output == "the answer"
    assert [c.name for t in result.turns for c in t.tool_calls] == ["load_skill", "search_issues"]
    assert result.success is True
    assert agent_result(_ending(), "g").output == "done: deployed"
    assert agent_result(_ending("needs_input"), "g").success is True
    stopped = agent_result(_ending("stopped"), "g")
    assert (stopped.success, stopped.cancelled) == (False, True)


async def test_dur2_s1_an_answered_run_learns_once() -> None:
    manager, spawned = _FakeSkillManager(), []
    out = await _step(manager, spawned).run(_ending())
    assert manager.learn_calls == [None]
    # Today's factory gives its agent no session id, so legacy records no activation counts; nor does durable.
    assert manager.activations == []
    assert out == {"success": True, "toolCalls": 2}


async def test_dur2_s1_with_a_session_the_loaded_skill_is_counted_once() -> None:
    manager, spawned = _FakeSkillManager(), []
    await _step(manager, spawned, session_id="ses-1").run(_ending())
    assert manager.activations == [("deploy", "ses-1")]
    assert manager.outcomes == [("deploy", "ses-1", True)]


async def test_dur2_s4_a_failed_run_records_a_failure_and_learns_nothing() -> None:
    manager, spawned = _FakeSkillManager(), []
    await _step(manager, spawned, session_id="ses-1").run(_ending("error", error_type="model_failed"))
    assert manager.outcomes == [("deploy", "ses-1", False)]
    assert manager.learn_calls == []


async def test_dur2_s5_an_approved_candidate_spawns_the_legacy_writer_once() -> None:
    manager, spawned = _FakeSkillManager([_candidate("a", "approved"), _candidate("b", "pending")]), []
    await _step(manager, spawned).run(_ending())
    assert len(spawned) == 1
    assert "skill-a" in spawned[0]


async def test_dur2_s5_the_writer_is_the_legacy_child_with_only_the_skill_tools() -> None:
    runtime = AsyncMock()
    runtime.tool_registry.names = lambda: ["skill_search", "skill_manage", "jira__create_issue"]
    await skill_writer(runtime, provider="langchain", model_name="m")("write it")
    spec, goal = runtime.run_child.await_args.args[:2]
    assert (spec.name, spec.tool_names, goal.description) == ("skill_writer", ["skill_search", "skill_manage"], "write it")


def _skills_on(services: w.Services) -> w.Services:
    services.config.put(
        PLATFORM_SETTINGS_KEY, {"featureFlags": {**RIG_PLATFORM_FLAGS, "ENABLE_MCP": True, "ENABLE_SKILLS": True}}
    )
    services.graph.get_nodes_by_filters = AsyncMock(return_value=[])
    return services


async def _post_run(services: w.Services, **spec: Any) -> list[str]:  # noqa: ANN401
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=Reply(text="ok"))])))
    runtime = await PipesHubRuntimeAssembler(services, model_factory=w.model_factory(port)).build(
        w.claim(w.pinned_spec(tool_ids=[], **spec))  # type: ignore[arg-type]
    )
    return [step.key for step in runtime.post_run]


@pytest.mark.usefixtures("_deployment")
async def test_dur2_s3_the_assembled_run_learns_with_skills_and_not_without(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "true")
    assert await _post_run(_skills_on(w.Services())) == [SKILL_LEARNING_STEP]
    assert await _post_run(w.Services()) == []


@pytest.mark.usefixtures("_deployment")
async def test_cleanup_g7_the_assembled_runs_learning_names_its_conversation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "true")
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=Reply(text="ok"))])))
    runtime = await PipesHubRuntimeAssembler(_skills_on(w.Services()), model_factory=w.model_factory(port)).build(
        w.claim(w.pinned_spec(tool_ids=[]))  # type: ignore[arg-type]
    )
    [step] = runtime.post_run
    # Candidates carry the conversation so its erasure takes them (CLEANUP-G7); usage counts stay off.
    assert step._learning._source_session_id == "ses-1"
    assert step._session_id is None


@pytest.fixture
def _deployment() -> Any:  # noqa: ANN401
    with w.deployment():
        yield
