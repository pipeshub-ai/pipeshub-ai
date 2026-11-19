"""`UsageReportingTransport`: every model call of a request reaches the
request's ledger, labelled by who made it. Each agent that calls a model
resolves it from the request's transports, including the ones that keep only
a response's data (critics, planners) and the ones that run as separate
agents (sub-agents, the intent call)."""

from __future__ import annotations

import asyncio
import gc
import logging
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from app.agent_loop_lib.agent.single_shot_runner import run_single_shot
from app.agent_loop_lib.core.exceptions import TransportError
from app.agent_loop_lib.core.messages import ToolCall, UserMessage
from app.agent_loop_lib.core.responses import TokenUsage
from app.agent_loop_lib.core.streaming import TextDeltaEvent
from app.agent_loop_lib.core.tool_schema import ToolSchema
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.hooks.middleware.builtin.auto_compact import make_llm_summarizer
from app.agent_loop_lib.modules.pipeline.critic.plan_critic import PlanCritic
from app.agent_loop_lib.modules.pipeline.critic.result_critic import ResultCritic
from app.agent_loop_lib.modules.pipeline.planner.base import Plan
from app.agent_loop_lib.modules.pipeline.planner.default import DefaultPlanner
from app.agent_loop_lib.modules.pipeline.planner.replanner import Replanner
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.agent_loop.usage_transport import UsageReportingTransport, with_usage_reporting
from app.utils.logger import JsonFormatter, TextFormatter, log_record_fields
from app.utils.logging_fields import log_fields
from tests.unit.agent_loop_lib.runtime.test_run_child_streaming import _child_spec
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.agent_loop_lib.core.streaming import StreamEvent

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

    def test_the_factory_always_wraps(self) -> None:
        transport = _scripted()

        wrapped = with_usage_reporting(lambda: transport, _Ledger().add)()

        assert isinstance(wrapped, UsageReportingTransport)
        assert wrapped._inner is transport


_TOOLS = [
    ToolSchema(name="search", description="d", input_schema={"type": "object"}),
    ToolSchema(name="fetch", description="d", input_schema={"type": "object"}),
]


def _end_lines(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == "llm_call.end"]


def _fields(record: logging.LogRecord) -> dict[str, object]:
    return log_record_fields(record)


class _FailsMidStream(ScriptedTransport):
    async def stream(self, *args: object, **kwargs: object) -> AsyncIterator[StreamEvent]:
        yield TextDeltaEvent(delta="model-answer-sentinel")
        raise TransportError("connection reset", status_code=502, retryable=True)


class TestEachCallLogsOneEndLine:
    async def test_complete_logs_ids_sizes_and_the_finish_reason(self, caplog: pytest.LogCaptureFixture) -> None:
        inner = ScriptedTransport().add_tool_call(ToolCall(id="c1", name="search", arguments={}), usage=_USAGE)
        with (
            patch("app.agents.agent_loop.usage_transport.time.perf_counter", side_effect=[10.0, 10.25]),
            caplog.at_level(logging.DEBUG, logger="app.agents.agent_loop.usage_transport"),
        ):
            await UsageReportingTransport(inner, _Ledger().add).complete([UserMessage(content="hi")], _TOOLS)

        [record] = _end_lines(caplog)
        assert record.levelno == logging.INFO
        assert _fields(record) == {
            "source": "main", "provider": "scripted", "model": "scripted-model", "duration_ms": 250,
            "tools_offered": 2, "tokens_in": 120, "tokens_out": 7, "cache_read_tokens": 100,
            "finish_reason": "end_turn", "tool_calls": 1, "outcome": "success",
        }

    async def test_a_stream_logs_once_at_its_final_event(self, caplog: pytest.LogCaptureFixture) -> None:
        inner = ScriptedTransport().add_text_chunks(["a", "b", "c"], usage=_USAGE)
        with caplog.at_level(logging.DEBUG, logger="app.agents.agent_loop.usage_transport"):
            events = [e async for e in UsageReportingTransport(inner, _Ledger().add).stream([UserMessage(content="hi")])]

        assert len(events) == 4
        [record] = _end_lines(caplog)
        assert _fields(record)["finish_reason"] == "end_turn"
        assert _fields(record)["tokens_out"] == 7

    async def test_a_stream_that_fails_midway_logs_the_failure_and_reraises(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        transport = UsageReportingTransport(_FailsMidStream(), _Ledger().add)
        with (
            caplog.at_level(logging.DEBUG, logger="app.agents.agent_loop.usage_transport"),
            pytest.raises(TransportError),
        ):
            _ = [e async for e in transport.stream([UserMessage(content="hi")], _TOOLS)]

        [record] = _end_lines(caplog)
        assert record.levelno == logging.DEBUG  # the retry or llm_call.failed line reports it
        fields = _fields(record)
        assert (fields["error_class"], fields["status"], fields["error_type"], fields["outcome"]) == (
            "upstream_5xx", 502, "TransportError", "failure",
        )
        assert "tokens_in" not in fields

    async def test_a_stream_closed_after_its_first_chunk_is_cancelled(self, caplog: pytest.LogCaptureFixture) -> None:
        inner = ScriptedTransport().add_text_chunks(["a", "b"], usage=_USAGE)
        stream = UsageReportingTransport(inner, _Ledger().add).stream([UserMessage(content="hi")])
        with caplog.at_level(logging.DEBUG, logger="app.agents.agent_loop.usage_transport"):
            await stream.__anext__()
            await stream.aclose()

        [record] = _end_lines(caplog)
        assert _fields(record)["error_class"] == "cancelled"

    async def test_a_consumer_cancelled_outside_the_stream_is_cancelled(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        inner = ScriptedTransport().add_text_chunks(["a", "b"], usage=_USAGE)
        transport = UsageReportingTransport(inner, _Ledger().add)
        first = asyncio.Event()

        async def consume() -> None:
            async for _event in transport.stream([UserMessage(content="hi")]):
                first.set()
                await asyncio.Event().wait()  # e.g. blocked on a full SSE queue

        with caplog.at_level(logging.DEBUG, logger="app.agents.agent_loop.usage_transport"):
            task = asyncio.create_task(consume())
            await first.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            del task
            gc.collect()
            for _ in range(3):  # the loop's async-generator finalizer closes it
                await asyncio.sleep(0)

        [record] = _end_lines(caplog)
        assert _fields(record)["error_class"] == "cancelled"

    async def test_complete_structured_has_no_finish_reason(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.DEBUG, logger="app.agents.agent_loop.usage_transport"):
            await _reporting(_Ledger()).complete_structured([UserMessage(content="hi")], {"type": "object"})

        [record] = _end_lines(caplog)
        fields = _fields(record)
        assert "finish_reason" not in fields and "tool_calls" not in fields
        assert fields["tools_offered"] == 0 and fields["tokens_in"] == 120

    async def test_a_provider_that_reported_no_usage_logs_no_tokens(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.DEBUG, logger="app.agents.agent_loop.usage_transport"):
            await UsageReportingTransport(ScriptedTransport(), _Ledger().add).complete([UserMessage(content="hi")])

        fields = _fields(_end_lines(caplog)[0])
        assert not {"tokens_in", "tokens_out", "cache_read_tokens"} & fields.keys()

    async def test_a_refused_key_is_misconfigured(self, caplog: pytest.LogCaptureFixture) -> None:
        inner = ScriptedTransport().add_error(TransportError("invalid api key", status_code=401))
        with (
            caplog.at_level(logging.DEBUG, logger="app.agents.agent_loop.usage_transport"),
            pytest.raises(TransportError),
        ):
            await UsageReportingTransport(inner, _Ledger().add).complete([UserMessage(content="hi")])

        fields = _fields(_end_lines(caplog)[0])
        assert (fields["error_class"], fields["status"]) == ("misconfigured", 401)

    async def test_the_bound_attempt_is_on_the_line(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.DEBUG, logger="app.agents.agent_loop.usage_transport"), log_fields(attempt=2):
            await _reporting(_Ledger()).complete([UserMessage(content="hi")])

        assert _fields(_end_lines(caplog)[0])["attempt"] == 2

    async def test_the_line_never_carries_the_prompt_or_the_answer(self, caplog: pytest.LogCaptureFixture) -> None:
        inner = ScriptedTransport().add_text("model-answer-sentinel", usage=_USAGE)
        with caplog.at_level(logging.DEBUG, logger="app.agents.agent_loop.usage_transport"):
            await UsageReportingTransport(inner, _Ledger().add).complete(
                [UserMessage(content="prompt-sentinel")], None, "system-sentinel",
            )

        [record] = _end_lines(caplog)
        for formatter in (TextFormatter(), JsonFormatter()):
            line = formatter.format(record)
            assert "sentinel" not in line, line

    async def test_a_finished_call_is_info_and_a_failed_attempt_is_debug(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        inner = ScriptedTransport().add_text("ok", usage=_USAGE).add_error(
            TransportError("slow down", status_code=429, retryable=True),
        )
        transport = UsageReportingTransport(inner, _Ledger().add)
        with caplog.at_level(logging.DEBUG, logger="app.agents.agent_loop.usage_transport"):
            await transport.complete([UserMessage(content="hi")])
            with pytest.raises(TransportError):
                await transport.complete([UserMessage(content="hi")])
            inner.add_error(TransportError("down", status_code=503))
            with pytest.raises(TransportError):
                await transport.complete_structured([UserMessage(content="hi")], {"type": "object"})

        assert [(r.levelno, _fields(r)["outcome"]) for r in _end_lines(caplog)] == [
            (logging.INFO, "success"), (logging.DEBUG, "failure"), (logging.DEBUG, "failure"),
        ]

    async def test_an_unnamed_model_is_left_out(self, caplog: pytest.LogCaptureFixture) -> None:
        inner = ScriptedTransport()
        inner._model = ""
        with caplog.at_level(logging.DEBUG, logger="app.agents.agent_loop.usage_transport"):
            await UsageReportingTransport(inner, _Ledger().add).complete([UserMessage(content="hi")])

        assert "model" not in _fields(_end_lines(caplog)[0])

    async def test_a_logging_failure_keeps_the_response(self) -> None:
        with patch("app.agents.agent_loop.usage_transport.log_event", side_effect=RuntimeError("log bug")):
            response = await _reporting(_Ledger(), "answer").complete([UserMessage(content="hi")])

        assert response.message.text == "answer"


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
