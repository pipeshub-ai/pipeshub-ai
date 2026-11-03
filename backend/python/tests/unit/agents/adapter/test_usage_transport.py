"""`UsageReportingTransport`: every model call of a request reaches the
request's ledger, labelled by who made it. Each agent that calls a model
resolves it from the request's transports, including the ones that keep only
a response's data (critics, planners) and the ones that run as separate
agents (sub-agents, the intent call)."""

from __future__ import annotations

from app.agent_loop_lib.agent.single_shot_runner import run_single_shot
from app.agent_loop_lib.core.messages import UserMessage
from app.agent_loop_lib.core.responses import TokenUsage
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.hooks.middleware.builtin.auto_compact import make_llm_summarizer
from app.agent_loop_lib.modules.pipeline.critic.plan_critic import PlanCritic
from app.agent_loop_lib.modules.pipeline.critic.result_critic import ResultCritic
from app.agent_loop_lib.modules.pipeline.planner.base import Plan
from app.agent_loop_lib.modules.pipeline.planner.default import DefaultPlanner
from app.agent_loop_lib.modules.pipeline.planner.replanner import Replanner
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.agent_loop.usage_transport import UsageReportingTransport, with_usage_reporting
from tests.unit.agent_loop_lib.runtime.test_run_child_streaming import _child_spec
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

_USAGE = TokenUsage(input_tokens=120, output_tokens=7, cache_read_tokens=100)


class _Ledger:
    def __init__(self) -> None:
        self.entries: list[tuple[str, TokenUsage]] = []

    def add(self, source: str, usage: TokenUsage) -> None:
        self.entries.append((source, usage))

    @property
    def sources(self) -> list[str]:
        return [source for source, _ in self.entries]


def _scripted(*texts: str) -> ScriptedTransport:
    transport = ScriptedTransport()
    for text in texts or ("done",):
        transport.add_text(text, usage=_USAGE)
    return transport


def _reporting(ledger: _Ledger, *texts: str) -> UsageReportingTransport:
    return UsageReportingTransport(_scripted(*texts), ledger.add)


class TestEachCallIsReported:
    async def test_complete(self) -> None:
        ledger = _Ledger()

        await _reporting(ledger).complete([UserMessage(content="hi")])

        assert ledger.entries == [("main", _USAGE)]

    async def test_complete_structured(self) -> None:
        ledger = _Ledger()

        await _reporting(ledger).complete_structured([UserMessage(content="hi")], {"type": "object"})

        assert ledger.entries == [("main", _USAGE)]

    async def test_a_stream_reports_once_at_its_final_event(self) -> None:
        ledger = _Ledger()

        events = [event async for event in _reporting(ledger).stream([UserMessage(content="hi")])]

        assert events
        assert ledger.entries == [("main", _USAGE)]

    async def test_a_failing_ledger_keeps_the_response(self) -> None:
        def broken(_source: str, _usage: TokenUsage) -> None:
            raise RuntimeError("accounting bug")

        response = await UsageReportingTransport(_scripted("answer"), broken).complete(
            [UserMessage(content="hi")],
        )

        assert response.message.text == "answer"

    def test_no_sink_leaves_the_transport_unwrapped(self) -> None:
        transport = _scripted()

        assert with_usage_reporting(lambda: transport, None)() is transport


class TestCallsAreLabelledByWhoMadeThem:
    async def test_a_sub_agent(self) -> None:
        from app.agent_loop_lib.runtime.runtime import AgentRuntime
        from app.agent_loop_lib.tools.builtin.planning.task_complete import TaskCompleteTool
        from app.agent_loop_lib.tools.registry import ToolRegistry

        ledger = _Ledger()
        registry = TransportRegistry()
        registry.register("scripted", lambda: _reporting(ledger))
        tools = ToolRegistry()
        tools.register_tool(TaskCompleteTool())
        runtime = AgentRuntime(transport_registry=registry, tool_registry=tools)

        await runtime.run_child(_child_spec(), Goal(description="research X"), None)

        assert ledger.sources == ["sub_agent:researcher"]

    async def test_a_single_shot_helper(self) -> None:
        from app.agent_loop_lib.agent.single_shot_runner import build_task_complete_runtime

        ledger = _Ledger()
        registry = TransportRegistry()
        registry.register("scripted", lambda: _reporting(ledger))

        await run_single_shot(_child_spec(), build_task_complete_runtime(registry), Goal(description="classify"))

        assert ledger.sources == ["single_shot:researcher"]

    async def test_context_compaction(self) -> None:
        ledger = _Ledger()
        registry = TransportRegistry()
        registry.register("langchain", lambda: _reporting(ledger, "the summary"))

        summary = await make_llm_summarizer(registry, "langchain", "m")([UserMessage(content="hi")])

        assert summary == "the summary"
        assert ledger.sources == ["auto_compact"]

    async def test_critics_and_planners(self) -> None:
        ledger = _Ledger()
        model = _reporting(ledger, "1. step one", "1. step two", "verdict", "verdict")
        goal = Goal(description="compare the two contracts")
        plan = Plan(goal=goal, text="1. read both")

        await DefaultPlanner(model).plan(goal)
        await Replanner(model=model, prior_plan_text=plan.text).plan(goal)
        await PlanCritic(model).critique(plan)
        await ResultCritic(model).critique(_result(goal, "both contracts renew in May"))

        assert ledger.sources == ["planner", "replanner", "plan_critic", "result_critic"]


def _result(goal: Goal, output: str):  # noqa: ANN202
    from app.agent_loop_lib.core.types import AgentResult

    return AgentResult(goal=goal, success=True, output=output)
