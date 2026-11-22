"""A provider's or a driver's error text never leaves the agent loop through a run's result, its events, its
timeline, a tool result the model reads, or a middleware's denial. Only `AgentResult.error_detail` keeps it, for the
caller's user-facing classifier, and no dump, repr or encoder reaches that.

Each error carries `SENTINELS` (a prompt echo and an email) and `SECRET` (a key), as a provider's answer can."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import opik.jsonable_encoder
import pytest

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.exceptions import SpawnDepthError, spawn_failure_text
from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.events.base import AgentEvent, EventEmitter, EventType
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.hooks.middleware.pipeline import Pipeline
from app.agent_loop_lib.modules.stores.timeline.in_memory import InMemoryTimelineStore
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.base import Tool, ToolOutput
from app.agent_loop_lib.tools.builtin.coordination.spawn_agent import SpawnAgentTool
from app.agent_loop_lib.tools.builtin.planning.replan import ReplanTool
from app.agent_loop_lib.tools.executor import ToolExecutor
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.support.provider_errors import MARK, SECRET, SENTINELS, transport_error
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

pytestmark = pytest.mark.asyncio

_GOAL = Goal(description="What changed this week?")
_BODY = {"message": f"{MARK} key {SECRET}", "type": "invalid_request_error", "param": None, "code": "content_filter"}


def _leaks(value: Any) -> list[str]:  # noqa: ANN401
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return [s for s in (*SENTINELS, "F30SECRET") if s in text]


class _Recorder(EventEmitter):
    def __init__(self) -> None:
        self.events: list[AgentEvent] = []

    async def emit(self, event: AgentEvent) -> None:
        self.events.append(event)


def _spec(name: str, provider: str, tools: list[str] | None = None) -> AgentSpec:
    return AgentSpec(name=name, system_prompt=f"You are {name}.", tool_names=tools or [],
                     model=ModelSpec(provider=provider, model="m"), loop=ReActLoop(), max_turns=3)


def _runtime(transport: ScriptedTransport, registry: ToolRegistry | None = None, **kwargs: Any) -> AgentRuntime:  # noqa: ANN401
    transports = TransportRegistry()
    transports.register("scripted", lambda: transport)
    return AgentRuntime(transport_registry=transports, tool_registry=registry or ToolRegistry(), **kwargs)


class TestAFailedModelCall:
    async def _run(self, status: int = 400) -> tuple[Any, _Recorder, InMemoryTimelineStore]:
        recorder, timeline = _Recorder(), InMemoryTimelineStore()
        runtime = _runtime(ScriptedTransport().add_error(transport_error(status, _BODY)),
                           event_emitter=recorder, timeline_store=timeline)
        return await Agent(_spec("root", "scripted"), runtime).run(_GOAL), recorder, timeline

    async def test_the_error_is_why_and_the_detail_is_the_raw_text(self) -> None:
        result, _, _ = await self._run()
        assert result.end_event == "llm_call_failed"
        assert result.error == "LLM call failed: bad_request: content_filter (BadRequestError, HTTP 400)"
        assert result.error_code == "content_filter"
        assert result.error_detail is not None and SECRET in result.error_detail and MARK in result.error_detail

    async def test_no_dump_repr_or_encoder_carries_the_detail(self) -> None:
        result, _, _ = await self._run()
        for rendered in (result.model_dump(), result.model_dump_json(), repr(result), str(vars(result)),
                         opik.jsonable_encoder.encode({"result": result, "list": [result]})):
            assert _leaks(rendered) == []

    async def test_the_error_events_and_the_timeline_carry_only_why(self) -> None:
        _, recorder, timeline = await self._run(429)
        errors = [e.payload for e in recorder.events if e.event_type in (EventType.ERROR, EventType.RUN_ERROR)]
        assert errors and _leaks(errors) == []
        entries = [e.model_dump(mode="json") for e in timeline._entries]
        assert any(e["event_type"] == "llm_call_failed" for e in entries)
        assert _leaks(entries) == []

    async def test_the_class_the_run_records_is_unchanged(self) -> None:
        result, _, _ = await self._run(401)
        assert result.error_class == "misconfigured"


def _route_ctx(transport: ScriptedTransport) -> SimpleNamespace:
    """What `verify_result`, `critique_plan` and `create_plan` read from their route context."""
    return SimpleNamespace(runtime=_runtime(transport), spec=_spec("root", "scripted"), goal=_GOAL,
                           scope=SimpleNamespace(turn=SimpleNamespace(run=SimpleNamespace(get=lambda slot: None))))


class TestAPlanningToolWhoseModelCallFails:
    @pytest.mark.parametrize(("module", "cls", "args"), [
        ("verify_result", "VerifyResultTool", {"output": "draft"}),
        ("critique_plan", "CritiquePlanTool", {"plan": "1. look"}),
        ("create_plan", "CreatePlanTool", {}),
    ])
    async def test_its_result_is_why(self, module: str, cls: str, args: dict) -> None:
        import importlib

        tool = getattr(importlib.import_module(f"app.agent_loop_lib.tools.builtin.planning.{module}"), cls)()
        transport = ScriptedTransport().add_error(transport_error(400, _BODY))
        result = await tool.handle(ToolCall(id="c1", name=tool.name, arguments=args), _route_ctx(transport))
        assert result.is_error
        assert result.content == "bad_request: content_filter (BadRequestError, HTTP 400)"

    async def test_replan_through_a_run(self) -> None:
        transport = ScriptedTransport()
        transport.add_tool_call(ToolCall(id="replan", name="replan", arguments={"reason": "source down"}))
        transport.add_error(transport_error(400, _BODY))
        transport.add_text("Done.")
        registry = ToolRegistry()
        registry.register_tool(ReplanTool())
        await Agent(_spec("root", "scripted", ["replan"]), _runtime(transport, registry)).run(_GOAL)
        assert len(transport.calls) == 3
        next_request = json.dumps([m.model_dump(mode="json") for m in transport.calls[-1]["messages"]])
        assert _leaks(next_request) == []
        assert "content_filter" in next_request


class TestAChildThatCannotBeSpawned:
    async def test_the_parent_model_is_told_only_why(self) -> None:
        parent_t = ScriptedTransport()
        parent_t.add_tool_call(ToolCall(id="c1", name="spawn_agent", arguments={"role": "researcher", "goal": "find"}))
        parent_t.add_text("Done.")
        registry = ToolRegistry()
        registry.register_tool(SpawnAgentTool())

        def broken_spec(role: str, **_: Any) -> AgentSpec:  # noqa: ANN401
            raise RuntimeError(f"config store refused: {MARK} {SECRET}")

        runtime = _runtime(parent_t, registry, spec_factory=broken_spec)
        await Agent(_spec("root", "scripted", ["spawn_agent"]), runtime).run(_GOAL)
        assert len(parent_t.calls) == 2
        assert _leaks([m.model_dump(mode="json") for m in parent_t.calls[-1]["messages"]]) == []

    async def test_a_refusal_keeps_its_own_words(self) -> None:
        assert spawn_failure_text(SpawnDepthError("Maximum spawn depth (3) reached")) == "Maximum spawn depth (3) reached"
        assert spawn_failure_text(RuntimeError(SECRET)) == "internal (RuntimeError)"


class _Probe(Tool):
    name = property(lambda self: "probe")
    short_description = property(lambda self: "probe")
    description = property(lambda self: "probe")
    path = property(lambda self: "/toolsets/test/probe")
    parameters = property(lambda self: [])

    async def execute(self, **kwargs: Any) -> ToolOutput:  # noqa: ANN401
        return ToolOutput(success=True, data="ran")


async def _raising(ctx: Any, next_fn: Any) -> None:  # noqa: ANN401
    raise ConnectionError(f"store at db.internal said: {MARK} {SECRET}")


class TestAMiddlewareThatRaises:
    async def test_a_pre_turn_gate_blocks_the_run_with_only_why(self) -> None:
        agent = Agent(_spec("root", "scripted"), _runtime(ScriptedTransport().add_text("unused")))
        agent.runtime.hooks.on(HookEvent.PRE_TURN).use(_raising)
        result = await agent.run(_GOAL)
        assert (result.success, result.end_event) == (False, "turn_blocked")
        assert "middleware error: unavailable (ConnectionError)" in result.error
        assert _leaks(result.error) == []

    async def test_a_tool_gate_denies_the_call_with_only_why(self) -> None:
        from app.agent_loop_lib.hooks.registry import HookRegistry

        registry = ToolRegistry()
        registry.register_tool(_Probe())
        hooks = HookRegistry()
        hooks.on(HookEvent.PRE_TOOL_USE).use(_raising)
        result = await ToolExecutor(registry, kernel=hooks).call_tool(ToolCall(id="c1", name="probe", arguments={}))
        assert result.is_error and result.content == "middleware error: unavailable (ConnectionError)"

    async def test_a_fail_open_pipeline_records_only_why(self) -> None:
        ctx = SimpleNamespace(metadata={})
        pipeline = Pipeline(is_terminal=lambda _ctx: False, fail_closed=False)
        pipeline.use(_raising)
        await pipeline.dispatch(ctx)
        assert ctx.metadata["middleware_errors"] == ["unavailable (ConnectionError)"]


class TestLibraryOnlyPaths:
    """Paths PipesHub doesn't wire (the timeline store, detached spawns, ControlPlane's route_task, the dev server):
    the same rule holds there."""

    async def test_a_failed_wrap_up_turn_is_timelined_as_why(self) -> None:
        transport = ScriptedTransport().add_text_and_tool_call("Checking.", ToolCall(id="p1", name="probe", arguments={}))
        transport.add_error(transport_error(400, _BODY))
        registry = ToolRegistry()
        registry.register_tool(_Probe())
        timeline = InMemoryTimelineStore()
        spec = _spec("root", "scripted", ["probe"]).model_copy(update={"max_turns": 1})
        await Agent(spec, _runtime(transport, registry, timeline_store=timeline)).run(_GOAL)
        (entry,) = [e.model_dump(mode="json") for e in timeline._entries if e.event_type == "final_answer_turn_failed"]
        assert entry["summary"] == "Final-answer LLM call failed: bad_request: content_filter (BadRequestError, HTTP 400)"
        assert _leaks(entry) == []

    async def test_a_detached_child_that_cannot_start_is_recorded_as_why(self) -> None:
        from app.agent_loop_lib.agent.spawn_scheduler import SPAWN_RESULTS_SLOT

        parent_t = ScriptedTransport()
        parent_t.add_tool_call(ToolCall(id="c1", name="spawn_agent", arguments={
            "role": "researcher", "goal": "find", "reasoning": "independent", "detach": True,
        }))
        parent_t.add_text("Done.")
        registry = ToolRegistry()
        registry.register_tool(SpawnAgentTool())

        def broken_spec(role: str, **_: Any) -> AgentSpec:  # noqa: ANN401
            raise RuntimeError(f"config store refused: {MARK} {SECRET}")

        timeline = InMemoryTimelineStore()
        agent = Agent(_spec("root", "scripted", ["spawn_agent"]),
                      _runtime(parent_t, registry, spec_factory=broken_spec, timeline_store=timeline))
        await agent.run(_GOAL)
        await asyncio.gather(*list(agent._detached_tasks), return_exceptions=True)

        (recorded,) = agent.scope.get(SPAWN_RESULTS_SLOT).values()
        assert recorded.result.error.endswith("failed with an infrastructure error: internal (RuntimeError)")
        failed = [e.model_dump(mode="json") for e in timeline._entries if e.event_type == "spawn_agent_detached_failed"]
        assert failed and _leaks(failed) == []

    async def test_route_task_falls_back_to_solo_saying_only_why(self) -> None:
        from app.agent_loop_lib.tools.builtin.coordination.route_task import (
            classify_task,
        )

        model = SimpleNamespace(complete_structured=AsyncMock(side_effect=transport_error(429, _BODY)))
        assert await classify_task(model, _GOAL) == {
            "route": "solo",
            "reason": "classification failed, defaulted to solo: rate_limited: content_filter (RateLimitError, HTTP 429)",
        }

    async def test_the_dev_servers_error_frame_says_only_why(self) -> None:
        from app.agent_loop_lib.serve.app import _agent_event_stream

        async def failing_stream(goal: Goal) -> Any:  # noqa: ANN401
            raise transport_error(400, _BODY)
            yield  # pragma: no cover

        frames = [f async for f in _agent_event_stream(SimpleNamespace(stream=failing_stream), _GOAL)]
        assert frames[-1] == (b'event: error\ndata: {"error": "bad_request: content_filter (BadRequestError, HTTP 400)"}\n\n')
