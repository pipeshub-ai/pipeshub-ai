"""Tests for `agent/best_of_n.py` — the run-level best-of-N strategy that
`tools/builtin/coordination/best_of_n.py`'s special route delegates to.

Exercises `run_best_of_n` end to end with a fake `agent` double (only the
duck-typed surface `_run_one_candidate`/`_judge`/`obs.*` actually touch),
plus focused unit tests for `_judge`'s branches directly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent_loop_lib.agent.best_of_n import _judge, _run_one_candidate, run_best_of_n
from app.agent_loop_lib.agent.single_shot_runner import StructuredSingleShotError
from app.agent_loop_lib.core.types import AgentResult, Goal, ToolCall


def _result(output: str, *, success: bool = True, error: str | None = None) -> AgentResult:
    return AgentResult(goal=Goal(description="candidate goal"), output=output, success=success, error=error)


def _fake_agent(*, run_child_results, transport_registry: object | None = None) -> MagicMock:
    """A minimal double for the `agent` argument `run_best_of_n` takes.

    `obs.write_state`/`obs.append_timeline` are patched out in each test
    (real observability plumbing is covered by `agent/observability.py`'s
    own tests), so `agent` only needs the fields `_run_one_candidate`/
    `_judge`/the spec-resolution call actually read.
    """
    agent = MagicMock()
    agent.runtime.run_child = AsyncMock(side_effect=run_child_results)
    agent.runtime.transport_registry = transport_registry
    agent.runtime.opik_enabled = False
    agent.runtime.opik_project_name = None
    agent.runtime.spec_for_role = MagicMock(return_value="candidate-spec")
    agent.run_ctx = "run-ctx"
    agent.scope = "scope"
    agent.session_id = "sess-1"
    return agent


def _call(**arguments) -> ToolCall:
    arguments.setdefault("role", "researcher")
    return ToolCall(id="c1", name="best_of_n", arguments=arguments)


@pytest.fixture(autouse=True)
def _patch_observability():
    with patch("app.agent_loop_lib.agent.best_of_n.obs.write_state", new=AsyncMock()), \
         patch("app.agent_loop_lib.agent.best_of_n.obs.append_timeline", new=AsyncMock()):
        yield


class TestRunBestOfN:
    async def test_judge_selects_best_of_multiple_candidates(self) -> None:
        agent = _fake_agent(
            run_child_results=[_result("A"), _result("B"), _result("C")],
            transport_registry=object(),
        )
        call = _call(n=3, criteria="most correct", goal="do the thing")

        with patch(
            "app.agent_loop_lib.agent.single_shot_runner.run_structured_single_shot",
            new=AsyncMock(return_value={"winner_index": 2, "reason": "C is best"}),
        ):
            result = await run_best_of_n(agent, call, Goal(description="parent"), 0, "t0")

        assert result.is_error is False
        assert result.content["winner_index"] == 2
        assert result.content["output"] == "C"
        assert result.content["n"] == 3
        assert result.content["judge_reason"] == "C is best"
        assert agent.runtime.run_child.await_count == 3

    async def test_single_successful_candidate_skips_judge(self) -> None:
        agent = _fake_agent(
            run_child_results=[_result("A"), _result("", success=False, error="boom")],
            transport_registry=object(),
        )
        call = _call(n=2)

        result = await run_best_of_n(agent, call, Goal(description="parent"), 0, "t0")

        assert result.is_error is False
        assert result.content["output"] == "A"
        assert result.content["winner_index"] == 0
        assert "judge skipped" in result.content["judge_reason"]

    async def test_no_transport_registry_defaults_to_first_successful(self) -> None:
        agent = _fake_agent(
            run_child_results=[_result("A"), _result("B")],
            transport_registry=None,
        )
        call = _call(n=2)

        result = await run_best_of_n(agent, call, Goal(description="parent"), 0, "t0")

        assert result.content["winner_index"] == 0
        assert result.content["output"] == "A"
        assert "No model available" in result.content["judge_reason"]

    async def test_judge_structured_error_falls_back_to_first_successful(self) -> None:
        agent = _fake_agent(
            run_child_results=[_result("A"), _result("B")],
            transport_registry=object(),
        )
        call = _call(n=2)

        with patch(
            "app.agent_loop_lib.agent.single_shot_runner.run_structured_single_shot",
            new=AsyncMock(side_effect=StructuredSingleShotError("model unavailable")),
        ):
            result = await run_best_of_n(agent, call, Goal(description="parent"), 0, "t0")

        assert result.content["winner_index"] == 0
        assert result.content["output"] == "A"
        assert "Judge call failed" in result.content["judge_reason"]

    async def test_judge_generic_exception_is_caught_by_caller(self) -> None:
        """The outer `try/except` in `run_best_of_n` (distinct from `_judge`'s
        own internal handling of `StructuredSingleShotError`) must also
        tolerate an unexpected exception escaping `_judge` entirely."""
        agent = _fake_agent(
            run_child_results=[_result("A"), _result("B")],
            transport_registry=object(),
        )
        call = _call(n=2)

        with patch(
            "app.agent_loop_lib.agent.best_of_n._judge",
            new=AsyncMock(side_effect=RuntimeError("judge exploded")),
        ):
            result = await run_best_of_n(agent, call, Goal(description="parent"), 0, "t0")

        assert result.content["winner_index"] == 0
        assert "judge exploded" in result.content["judge_reason"]

    async def test_winner_index_maps_through_failed_candidates(self) -> None:
        """`local_index` from `_judge` indexes into `successful`, not
        `candidates` — with a failure in the middle, those two indices
        diverge and the mapping back to `candidates` must be correct."""
        agent = _fake_agent(
            run_child_results=[
                _result("", success=False, error="fail0"),
                _result("B"),
                _result("C"),
            ],
            transport_registry=object(),
        )
        call = _call(n=3)

        with patch(
            "app.agent_loop_lib.agent.single_shot_runner.run_structured_single_shot",
            new=AsyncMock(return_value={"winner_index": 1, "reason": "C wins"}),
        ):
            result = await run_best_of_n(agent, call, Goal(description="parent"), 0, "t0")

        # successful = [(1, B), (2, C)]; local_index=1 -> candidates[2] == "C"
        assert result.content["winner_index"] == 2
        assert result.content["output"] == "C"

    async def test_all_candidates_fail_returns_error_result(self) -> None:
        agent = _fake_agent(
            run_child_results=[
                _result("", success=False, error="err-a"),
                _result("", success=False, error="err-b"),
            ],
            transport_registry=object(),
        )
        call = _call(n=2)

        result = await run_best_of_n(agent, call, Goal(description="parent"), 0, "t0")

        assert result.is_error is True
        assert "All 2 candidates failed" in result.content
        assert "err-a" in result.content
        assert "err-b" in result.content
        agent.runtime.spec_for_role.assert_called_once()

    async def test_n_is_clamped_between_two_and_five(self) -> None:
        agent = _fake_agent(
            run_child_results=[_result("A"), _result("B")],
            transport_registry=None,
        )
        call = _call(n=1)  # below the floor

        result = await run_best_of_n(agent, call, Goal(description="parent"), 0, "t0")

        assert result.content["n"] == 2
        assert agent.runtime.run_child.await_count == 2

    async def test_explicit_tools_and_model_overrides_passed_through(self) -> None:
        agent = _fake_agent(run_child_results=[_result("A"), _result("B")], transport_registry=None)
        call = _call(n=2, tools=["web_search"], model="gpt-5")

        await run_best_of_n(agent, call, Goal(description="parent"), 0, "t0")

        agent.runtime.spec_for_role.assert_called_once_with(
            "researcher", tool_names=["web_search"], model="gpt-5",
        )

    async def test_no_overrides_omitted_from_spec_for_role_kwargs(self) -> None:
        agent = _fake_agent(run_child_results=[_result("A"), _result("B")], transport_registry=None)
        call = _call(n=2)

        await run_best_of_n(agent, call, Goal(description="parent"), 0, "t0")

        agent.runtime.spec_for_role.assert_called_once_with("researcher")


class TestRunOneCandidate:
    async def test_exception_from_run_child_becomes_failed_result(self) -> None:
        agent = _fake_agent(run_child_results=[RuntimeError("infra failure")])
        goal = Goal(description="g")

        result = await _run_one_candidate(agent, "spec", goal, "team-1")

        assert result.success is False
        assert result.error == "infra failure"
        assert result.goal == goal

    async def test_success_passes_through(self) -> None:
        expected = _result("ok")
        agent = _fake_agent(run_child_results=[expected])

        result = await _run_one_candidate(agent, "spec", Goal(description="g"), "team-1")

        assert result is expected


class TestJudge:
    async def test_single_successful_short_circuits(self) -> None:
        agent = _fake_agent(run_child_results=[])
        index, reason = await _judge(agent, "goal desc", "criteria", [(0, _result("A"))])
        assert index == 0
        assert "skipped" in reason

    async def test_no_transport_registry_short_circuits(self) -> None:
        agent = _fake_agent(run_child_results=[], transport_registry=None)
        successful = [(0, _result("A")), (1, _result("B"))]
        index, reason = await _judge(agent, "goal desc", "criteria", successful)
        assert index == 0
        assert "No model available" in reason

    async def test_winner_index_out_of_range_is_clamped(self) -> None:
        agent = _fake_agent(run_child_results=[], transport_registry=object())
        successful = [(0, _result("A")), (1, _result("B"))]

        with patch(
            "app.agent_loop_lib.agent.single_shot_runner.run_structured_single_shot",
            new=AsyncMock(return_value={"winner_index": 99, "reason": "way out of range"}),
        ):
            index, reason = await _judge(agent, "goal desc", "criteria", successful)

        assert index == 1  # clamped to len(successful) - 1
        assert reason == "way out of range"

    async def test_missing_winner_index_defaults_to_zero(self) -> None:
        agent = _fake_agent(run_child_results=[], transport_registry=object())
        successful = [(0, _result("A")), (1, _result("B"))]

        with patch(
            "app.agent_loop_lib.agent.single_shot_runner.run_structured_single_shot",
            new=AsyncMock(return_value={}),
        ):
            index, reason = await _judge(agent, "goal desc", "criteria", successful)

        assert index == 0
        assert reason == ""
