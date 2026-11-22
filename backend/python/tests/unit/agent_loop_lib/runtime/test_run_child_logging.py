"""One `sub_agent.end` per child (`AgentRuntime.run_child`), each child's lines carrying its own
`agent_run_id` and its spawner's `parent_run_id`, and spawn refusals logged once at WARNING."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.context import RunContext
from app.agent_loop_lib.core.exceptions import TransportError
from app.agent_loop_lib.core.types import AgentResult, Goal, ToolCall
from app.agent_loop_lib.runtime.runtime import MAX_SPAWN_DEPTH, AgentRuntime
from app.agent_loop_lib.tools.base import Tool, ToolOutput
from app.agent_loop_lib.tools.builtin.coordination.spawn_agent import SpawnAgentTool
from app.agent_loop_lib.tools.builtin.planning.task_complete import TaskCompleteTool
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.utils.logger import log_record_fields
from app.utils.logging_fields import log_fields
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

pytestmark = pytest.mark.asyncio

GOAL_SENTINEL = "goal-sentinel-c41"
OUTPUT_SENTINEL = "output-sentinel-c41"
ARG_SENTINEL = "arg-sentinel-c41"
PROBE = logging.getLogger("tests.run_child_logging.probe")


class ProbeTool(Tool):
    """A child's tool: logs one line from inside the child."""

    name = property(lambda self: "probe__run")
    short_description = property(lambda self: "probe")
    description = property(lambda self: "probe")
    path = property(lambda self: "/toolsets/probe/probe__run")
    parameters = property(lambda self: [])

    async def execute(self, **kwargs: Any) -> ToolOutput:  # noqa: ANN401
        PROBE.info("probe.inside")
        return ToolOutput(success=True, data="probed")


class Harness:
    """A parent agent with `spawn_agent`, one scripted transport per role (so parallel children
    don't race for each other's steps)."""

    def __init__(self, *roles: str, broken_roles: tuple[str, ...] = ()) -> None:
        self.transports = {name: ScriptedTransport() for name in ("parent", *roles)}
        self.broken_roles = broken_roles
        self.registry = ToolRegistry()
        for tool in (SpawnAgentTool(), TaskCompleteTool(), ProbeTool()):
            self.registry.register_tool(tool)
        transports = TransportRegistry()
        for name, transport in self.transports.items():
            transports.register(name, lambda t=transport: t)
        self.runtime = AgentRuntime(transport_registry=transports, tool_registry=self.registry, spec_factory=self._spec_for)

    def _spec_for(self, role: str, **overrides: Any) -> AgentSpec:  # noqa: ANN401
        if role in self.broken_roles:
            raise ValueError(f"no role {role}")
        tools = list(overrides.get("tool_names") or ["probe__run", "spawn_agent"])
        return spec(role, tools, provider=role)

    def parent(self) -> Agent:
        return Agent(spec("parent", ["spawn_agent", "task_complete"], provider="parent"), self.runtime)


def spec(name: str, tools: list[str], *, provider: str) -> AgentSpec:
    return AgentSpec(
        name=name, system_prompt=f"You are {name}.", tool_names=tools,
        model=ModelSpec(provider=provider, model="m"), loop=ReActLoop(), max_turns=4,
    )


def spawn(call_id: str, role: str, **extra: Any) -> ToolCall:  # noqa: ANN401
    return ToolCall(id=call_id, name="spawn_agent", arguments={
        "role": role, "goal": f"{GOAL_SENTINEL} {call_id}", "reasoning": "independent", **extra,
    })


async def run_turn(agent: Agent, run_id: str = "turn-1") -> AgentResult:
    with log_fields(run_id=run_id):
        result = await agent.run(Goal(description=f"{GOAL_SENTINEL} parent"))
        if agent._detached_tasks:
            await asyncio.gather(*list(agent._detached_tasks), return_exceptions=True)
    return result


def named(caplog: pytest.LogCaptureFixture, event: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == event]


def fields(record: logging.LogRecord) -> dict[str, object]:
    return log_record_fields(record)


def warn_plus(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno >= logging.WARNING]


class TestOneLinePerChild:
    async def test_parallel_children_each_end_once_with_their_own_run(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        h = Harness("researcher")
        h.transports["parent"].add_tool_calls([spawn("call_a", "researcher"), spawn("call_b", "researcher")]).add_text("done")
        h.transports["researcher"].add_text(OUTPUT_SENTINEL).add_text(OUTPUT_SENTINEL)
        parent = h.parent()

        await run_turn(parent)

        ends = named(caplog, "sub_agent.end")
        assert [(r.levelno, fields(r)["outcome"]) for r in ends] == [(logging.INFO, "success")] * 2
        assert len({fields(r)["agent_run_id"] for r in ends}) == 2
        assert {fields(r)["parent_run_id"] for r in ends} == {parent.run_ctx.run_id}
        assert sorted(fields(r)["tool_call_id"] for r in ends) == ["call_a", "call_b"]
        assert {(fields(r)["role"], fields(r)["spawn_depth"], fields(r)["run_id"]) for r in ends} == {("researcher", 1, "turn-1")}

    async def test_lines_inside_a_child_carry_its_run_and_none_leak_out(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        h = Harness("researcher")
        h.transports["parent"].add_tool_call(spawn("call_a", "researcher")).add_text("done")
        h.transports["researcher"].add_tool_call(ToolCall(id="p1", name="probe__run", arguments={})).add_text("ok")

        await run_turn(h.parent())
        PROBE.info("probe.after")

        (end,) = named(caplog, "sub_agent.end")
        (inside,) = named(caplog, "probe.inside")
        child_tool_end = next(r for r in named(caplog, "tool_call.end") if fields(r)["tool"] == "probe__run")
        assert fields(inside)["agent_run_id"] == fields(child_tool_end)["agent_run_id"] == fields(end)["agent_run_id"]
        parent_tool_end = next(r for r in named(caplog, "tool_call.end") if fields(r)["tool"] == "spawn_agent")
        assert "agent_run_id" not in fields(parent_tool_end)
        assert "agent_run_id" not in fields(named(caplog, "probe.after")[0])

    async def test_a_grandchilds_parent_is_the_child(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        h = Harness("mid", "leaf")
        h.transports["parent"].add_tool_call(spawn("call_m", "mid")).add_text("done")
        h.transports["mid"].add_tool_call(spawn("call_l", "leaf")).add_text("mid done")
        h.transports["leaf"].add_text("leaf done")

        await run_turn(h.parent())

        ends = {fields(r)["role"]: fields(r) for r in named(caplog, "sub_agent.end")}
        assert ends["leaf"]["parent_run_id"] == ends["mid"]["agent_run_id"]
        assert (ends["leaf"]["spawn_depth"], ends["leaf"]["tool_call_id"]) == (2, "call_l")


class TestTheParentId:
    async def test_a_given_parent_context_wins_with_nothing_bound(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        h = Harness("worker")
        h.transports["worker"].add_text("ok")
        parent_ctx = RunContext(role_name="cp", model="m")

        await h.runtime.run_child(spec("worker", [], provider="worker"), Goal(description="g"), parent_ctx)

        assert fields(named(caplog, "sub_agent.end")[0])["parent_run_id"] == parent_ctx.run_id

    @pytest.mark.parametrize("bound,expected", [
        ({"run_id": "turn-9"}, "turn-9"),
        ({"run_id": "turn-9", "agent_run_id": "child-1"}, "child-1"),
    ])
    async def test_without_a_parent_context_the_bound_ids_are_the_parent(
        self, bound: dict[str, str], expected: str, caplog: pytest.LogCaptureFixture,
    ) -> None:
        # The shape of AgentTool.execute and skills_wiring's skill writer: run_child(..., None).
        caplog.set_level(logging.DEBUG)
        h = Harness("worker")
        h.transports["worker"].add_text("ok")

        with log_fields(**bound):
            await h.runtime.run_child(spec("worker", [], provider="worker"), Goal(description="g"), None)

        (end,) = named(caplog, "sub_agent.end")
        assert fields(end)["parent_run_id"] == expected and fields(end)["agent_run_id"] != expected


class TestHowAChildEnded:
    async def test_a_failed_child_ends_failed_and_its_model_failure_is_the_only_warning(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.DEBUG)
        h = Harness("researcher")
        h.transports["parent"].add_tool_call(spawn("call_f", "researcher")).add_text("done")
        h.transports["researcher"].add_error(TransportError("upstream exploded", status_code=500))

        await run_turn(h.parent())

        (end,) = named(caplog, "sub_agent.end")
        assert end.levelno == logging.INFO
        assert (fields(end)["outcome"], fields(end)["error_class"], fields(end)["end_event"]) == ("failure", "upstream_5xx", "llm_call_failed")
        assert [r.getMessage() for r in warn_plus(caplog)] == ["llm_call.failed"]
        assert fields(warn_plus(caplog)[0])["agent_run_id"] == fields(end)["agent_run_id"]

    async def test_a_child_that_raises_is_a_record_and_the_spawn_call_is_the_failure_line(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        caplog.set_level(logging.DEBUG)
        real_run = Agent.run

        async def _run(self: Agent, goal: Goal, **kwargs: Any) -> AgentResult:  # noqa: ANN401
            if self.spec.name == "boom":
                raise RuntimeError(GOAL_SENTINEL)
            return await real_run(self, goal, **kwargs)

        monkeypatch.setattr(Agent, "run", _run)
        h = Harness("boom")
        h.transports["parent"].add_tool_call(spawn("call_x", "boom")).add_text("done")

        await run_turn(h.parent())

        (end,) = named(caplog, "sub_agent.end")
        assert (end.levelno, fields(end)["outcome"], fields(end)["error_type"]) == (logging.INFO, "failure", "RuntimeError")
        (failed,) = warn_plus(caplog)
        assert failed.getMessage() == "tool_call.end"
        assert (fields(failed)["reason"], fields(failed)["error_class"]) == ("exception", "internal")

    async def test_a_cancelled_child_ends_cancelled_and_the_cancel_propagates(self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
        caplog.set_level(logging.DEBUG)

        async def _run(self: Agent, goal: Goal, **kwargs: Any) -> AgentResult:  # noqa: ANN401
            raise asyncio.CancelledError

        monkeypatch.setattr(Agent, "run", _run)
        h = Harness("worker")
        with pytest.raises(asyncio.CancelledError):
            await h.runtime.run_child(spec("worker", [], provider="worker"), Goal(description="g"), None)
        assert fields(named(caplog, "sub_agent.end")[0])["error_class"] == "cancelled"

    async def test_a_child_that_needs_input_is_partial(self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
        caplog.set_level(logging.DEBUG)

        async def _run(self: Agent, goal: Goal, **kwargs: Any) -> AgentResult:  # noqa: ANN401
            return AgentResult(goal=goal, success=True, output="half", needs_input="the account")

        monkeypatch.setattr(Agent, "run", _run)
        await Harness("worker").runtime.run_child(spec("worker", [], provider="worker"), Goal(description="g"), None)
        assert fields(named(caplog, "sub_agent.end")[0])["outcome"] == "partial"


class TestRefusals:
    async def test_the_depth_limit_is_one_warning_without_a_stack(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        h = Harness("researcher")
        h.transports["parent"].add_tool_call(spawn("call_x", "researcher")).add_text("done")
        parent = h.parent()
        parent._run_ctx.spawn_depth = MAX_SPAWN_DEPTH

        await run_turn(parent)

        assert not named(caplog, "sub_agent.end")
        (failed,) = warn_plus(caplog)
        assert (failed.getMessage(), failed.levelno, failed.exc_text) == ("tool_call.end", logging.WARNING, None)
        assert (fields(failed)["reason"], fields(failed)["error_class"]) == ("max_spawn_depth", "bad_request")

    async def test_a_batch_validation_error_is_a_dependency_refusal(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        h = Harness("researcher")
        h.transports["parent"].add_tool_calls([
            spawn("call_a", "researcher", task_id="t1"), spawn("call_b", "researcher", task_id="t1"),
        ]).add_text("done")
        h.transports["researcher"].add_text("ok").add_text("ok")

        await run_turn(h.parent())

        refused = [r for r in warn_plus(caplog) if r.getMessage() == "tool_call.end"]
        assert refused and all(
            (r.levelno, r.exc_text, fields(r)["reason"], fields(r)["error_class"]) == (logging.WARNING, None, "dependency_failed", "bad_request")
            for r in refused
        )

    async def test_a_failed_prerequisite_is_a_dependency_refusal(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        h = Harness("researcher")
        h.transports["parent"].add_tool_calls([
            spawn("call_a", "researcher", task_id="first"), spawn("call_b", "researcher", task_id="second", depends_on=["first"]),
        ]).add_text("done")
        h.transports["researcher"].add_error(TransportError("upstream exploded", status_code=500))

        await run_turn(h.parent())

        (refused,) = [r for r in named(caplog, "tool_call.end") if fields(r)["tool_call_id"] == "call_b"]
        assert (refused.levelno, fields(refused)["reason"], fields(refused)["error_class"]) == (logging.WARNING, "dependency_failed", "bad_request")


class TestDetached:
    async def test_a_detached_child_that_cannot_start_is_logged_once(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        h = Harness(broken_roles=("broken",))
        h.transports["parent"].add_tool_call(spawn("call_d", "broken", detach=True)).add_text("done")

        await run_turn(h.parent())

        (failed,) = warn_plus(caplog)
        assert failed.getMessage() == "sub_agent.detached_failed"
        assert (fields(failed)["error_class"], fields(failed)["tool_call_id"], fields(failed)["role"]) == ("internal", "call_d", "broken")
        assert "error_message" not in fields(failed)

    async def test_a_detached_child_at_the_depth_limit_is_a_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        h = Harness("researcher")
        h.transports["parent"].add_tool_call(spawn("call_d", "researcher", detach=True)).add_text("done")
        parent = h.parent()
        parent._run_ctx.spawn_depth = MAX_SPAWN_DEPTH

        await run_turn(parent)

        (failed,) = warn_plus(caplog)
        assert (failed.getMessage(), failed.levelno, failed.exc_text) == ("sub_agent.detached_failed", logging.WARNING, None)
        assert (fields(failed)["reason"], fields(failed)["error_class"]) == ("max_spawn_depth", "bad_request")


class TestNoContent:
    async def test_goals_outputs_and_arguments_are_never_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        h = Harness("researcher")
        h.transports["parent"].add_tool_call(spawn("call_a", "researcher", note=ARG_SENTINEL)).add_text("done")
        h.transports["researcher"].add_text(OUTPUT_SENTINEL)

        await run_turn(h.parent())

        assert named(caplog, "sub_agent.end")
        text = caplog.text + " ".join(str(fields(r)) for r in caplog.records)
        assert GOAL_SENTINEL not in text and OUTPUT_SENTINEL not in text and ARG_SENTINEL not in text
