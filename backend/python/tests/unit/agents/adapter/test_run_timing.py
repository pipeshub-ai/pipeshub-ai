"""The agent route's turn end line (`stream_bridge.run_agent_loop_stream`).

One `agent_turn.end` per turn says how it ended (stop_reason), how much it did
(turns, tool calls, model calls and tokens from the request's ledger) and, with
`PIPESHUB_CHAT_TIMING` on, where the time went (`spans`: each model turn, each
tool call, the pattern-match wait inside a search, the finish step). These tests
drive `run_agent_loop_stream` with a fake agent and read that line through a real
logger.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest
from langchain_core.messages import AIMessageChunk

from app.agent_loop_lib.core.responses import TokenUsage
from app.agent_loop_lib.core.types import AgentResult, Goal
from app.agent_loop_lib.events.base import AgentEvent, EventType, RunContext
from app.agents.agent_loop.langchain_transport import LangChainTransport
from app.agents.agent_loop.stream_bridge import run_agent_loop_stream
from app.agents.agent_loop.tool_approvals import _PENDING_IN_RUN
from app.agents.agent_loop.usage_transport import UsageReportingTransport
from app.utils.logger import log_record_fields
from app.utils.pattern_match import await_pattern_match
from app.utils.stage_timer import StageTimer
from tests.unit.agent_loop_lib.agent.test_agent_step_outcomes import _build
from tests.unit.agents.adapter.support.provider_stall import pending_since
from tests.unit.agents.adapter.test_langchain_transport import _StallingModel

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable

    from app.agents.agent_loop.context import AgentContext

_ROOT = RunContext(role_name="pipeshub-agent", model="gpt-4")
_CHILD = _ROOT.child(role_name="internal_exploration_agent")
_LOG = logging.getLogger("tests.run_timing")
_RUN_ID = "0b5c1f6e-1d2a-4c3b-9e8f-7a6b5c4d3e2f"
_QUERY = "query-sentinel"


def _ev(event_type: EventType, payload: dict[str, Any], ctx: RunContext = _ROOT) -> AgentEvent:
    return AgentEvent(event_type=event_type, run_context=ctx, payload=payload)


def _result(**fields: object) -> AgentResult:
    return AgentResult(goal=Goal(description="g"), **fields)


def _two_turn_agent(context: AgentContext) -> MagicMock:
    """Turn 1 calls a search tool that waits on a pattern match; turn 2
    answers. A sub-agent's turn in the middle must not be counted."""
    agent = MagicMock()
    agent.last_stream_result = None

    async def _fake_stream(goal: object, **kwargs: object) -> AsyncIterator[AgentEvent]:
        yield _ev(EventType.TURN_START, {"turn_index": 0})
        yield _ev(EventType.TEXT_MESSAGE_START, {"turn_index": 0})
        yield _ev(EventType.TEXT_MESSAGE_END, {"turn_index": 0})
        context.usage_ledger.add("main", TokenUsage(input_tokens=100, output_tokens=10, cache_read_tokens=80))
        yield _ev(EventType.TOOL_CALL, {"tool": "retrieval", "tool_call_id": "c1"})
        logging.getLogger("tests.run_timing.tool").info("tool ran")
        # What the retrieval tool does with the shared tool state.
        pattern_task = asyncio.create_task(asyncio.sleep(0, result=[]))
        await await_pattern_match(
            pattern_task, MagicMock(), run_timer=context.tool_state.get("_run_timer"),
        )
        yield _ev(EventType.TURN_START, {"turn_index": 0}, ctx=_CHILD)
        yield _ev(EventType.TEXT_MESSAGE_END, {"turn_index": 0}, ctx=_CHILD)
        yield _ev(EventType.TOOL_RESULT, {"tool": "retrieval", "tool_call_id": "c1", "is_error": True}, ctx=_CHILD)
        yield _ev(EventType.TOOL_RESULT, {"tool": "retrieval", "tool_call_id": "c1", "is_error": False})
        yield _ev(EventType.TURN_COMPLETE, {"turn_index": 0})
        yield _ev(EventType.TURN_START, {"turn_index": 1})
        yield _ev(EventType.TEXT_MESSAGE_START, {"turn_index": 1})
        yield _ev(EventType.TEXT_MESSAGE_END, {"turn_index": 1})
        context.usage_ledger.add("main", TokenUsage(input_tokens=200, output_tokens=20))
        yield _ev(EventType.TURN_COMPLETE, {"turn_index": 1})
        yield _ev(EventType.AGENT_COMPLETE, {})
        agent.last_stream_result = _result(output="42", end_event="agent_complete")

    agent.stream = _fake_stream
    return agent


def _ending_with(result: AgentResult, *, before: Callable[[AgentContext], None] | None = None):  # noqa: ANN202
    """One model turn that raised or ran out, then `result`."""
    def make(context: AgentContext) -> MagicMock:
        agent = MagicMock()
        agent.last_stream_result = None

        async def _fake_stream(goal: object, **kwargs: object) -> AsyncIterator[AgentEvent]:
            yield _ev(EventType.TURN_START, {"turn_index": 0})
            if before is not None:
                before(context)
            yield _ev(EventType.ERROR, {"error": "x"})
            agent.last_stream_result = result

        agent.stream = _fake_stream
        return agent

    return make


def _hanging_tool_agent(context: AgentContext) -> MagicMock:
    """A tool call that is still running when the client goes away."""
    agent = MagicMock()
    agent.last_stream_result = None

    async def _fake_stream(goal: object, **kwargs: object) -> AsyncIterator[AgentEvent]:
        yield _ev(EventType.TURN_START, {"turn_index": 0})
        yield _ev(EventType.TEXT_MESSAGE_END, {"turn_index": 0})
        yield _ev(EventType.TOOL_CALL, {"tool": "retrieval", "tool_call_id": "c1"})
        await asyncio.sleep(30)

    agent.stream = _fake_stream
    return agent


async def _run(
    stage_timer: StageTimer | None,
    *,
    patch_finalizer: bool = True,
    make_agent: Callable[[AgentContext], MagicMock] | None = None,
    disconnect_after_first_frame: bool = False,
    clarifying_questions: list[Any] | None = None,
    build_state: dict[str, Any] | None = None,
    goal: Goal | None = None,
    drive: Callable[[AsyncGenerator[str, None]], Awaitable[list[str]]] | None = None,
) -> list[str]:
    make_agent = make_agent or _two_turn_agent

    async def _fake_create(  # noqa: ANN202 - mirrors PipesHubAgentFactory.create
        self, context, llm, chat_mode, *, query, model_name="", session_id=None, model_key=None,
    ):
        await context.event_sink.write({"event": "status", "data": {"status": "planning"}})
        return make_agent(context), MagicMock(), goal or MagicMock(description="g"), clarifying_questions or []

    async def _fake_finalizer_run(self, *, event_sink, **kwargs) -> None:
        await event_sink.write({"event": "complete", "data": {"answer": "42"}})

    state = build_state if build_state is not None else {"org_id": "org-1", "user_id": "user-1", "query": _QUERY}
    with (
        patch("app.modules.agents.qna.chat_state.build_initial_state", return_value=state),
        patch(
            "app.utils.connector_instances.fetch_user_connector_instances",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.agents.agent_loop.stream_bridge.PipesHubAgentFactory.create",
            new=_fake_create,
        ),
        patch(
            "app.agents.agent_loop.stream_bridge.AnswerFinalizer.run",
            new=_fake_finalizer_run,
        ) if patch_finalizer else contextlib.nullcontext(),
        patch("app.agents.agent_loop.stream_bridge.emit_pre_run_clarification", new=AsyncMock()),
    ):
        stream = run_agent_loop_stream(
            query_info={"query": _QUERY, "chatMode": "quick", "runId": _RUN_ID},
            user_info={"userId": "user-1", "orgId": "org-1"},
            llm=MagicMock(), log=_LOG, retrieval_service=MagicMock(),
            graph_provider=MagicMock(), reranker_service=MagicMock(),
            config_service=MagicMock(), model_name="gpt-4",
            stage_timer=stage_timer,
        )
        if drive is not None:
            chunks = await drive(stream)
        elif disconnect_after_first_frame:
            chunks = [await stream.__anext__()]
            await asyncio.sleep(0.05)  # let the agent reach its tool call
            await stream.aclose()  # the client goes away
        else:
            chunks = [chunk async for chunk in stream]
    return chunks


def _end_lines(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == "agent_turn.end"]


def _warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """WARN+ lines of the turn (not the fixtures' unrelated demo-data lookup)."""
    return [r for r in caplog.records if r.levelno >= logging.WARNING and r.name.startswith(_LOG.name)]


def _end_fields(caplog: pytest.LogCaptureFixture) -> dict[str, Any]:
    [record] = _end_lines(caplog)
    return log_record_fields(record)


@pytest.fixture
def caplog(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.DEBUG)
    return caplog


class TestTheTurnEndLine:
    async def test_a_two_turn_run_logs_what_it_did_and_where_the_time_went(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        await _run(StageTimer(enabled=True))

        [record] = _end_lines(caplog)
        assert record.levelno == logging.INFO
        fields = log_record_fields(record)
        assert {k: fields[k] for k in (
            "stop_reason", "end_event", "run_id", "model", "mode", "turns", "tool_calls", "tool_errors",
            "llm_calls", "tokens_in", "tokens_out", "cache_read_tokens",
        )} == {
            # The sub-agent's turn and failed tool are inside the parent's tool span, not the parent's own.
            "stop_reason": "done", "end_event": "agent_complete", "run_id": _RUN_ID, "model": "gpt-4",
            "mode": "quick", "turns": 2, "tool_calls": 1, "tool_errors": 0,
            "llm_calls": 2, "tokens_in": 300, "tokens_out": 30, "cache_read_tokens": 80,
        }
        assert isinstance(fields["duration_ms"], int)
        assert re.findall(r"(\S+?)=\d+ms", fields["spans"]) == [
            "llm_turn_1", "pattern_match_wait", "tool:retrieval", "llm_turn_2", "finish",
        ]

    async def test_the_first_event_line_is_still_logged_without_an_emoji(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        await _run(StageTimer(enabled=True))
        assert any(r.getMessage().startswith("agent chat stream total=") for r in caplog.records)

    @pytest.mark.parametrize("stage_timer", [StageTimer(enabled=False), None])
    async def test_timing_off_still_logs_the_counts_without_spans(
        self, caplog: pytest.LogCaptureFixture, stage_timer: StageTimer | None,
    ) -> None:
        await _run(stage_timer)

        fields = _end_fields(caplog)
        assert (fields["turns"], fields["tool_calls"], fields["llm_calls"]) == (2, 1, 2)
        assert "spans" not in fields

    async def test_lines_inside_the_run_carry_run_id_and_nothing_leaks_out(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        await _run(None)
        _LOG.info("after the stream")

        by_message = {r.getMessage(): log_record_fields(r) for r in caplog.records}
        assert by_message["tool ran"]["run_id"] == _RUN_ID
        assert "run_id" not in by_message["after the stream"]

    async def test_the_line_never_carries_the_query(self, caplog: pytest.LogCaptureFixture) -> None:
        await _run(StageTimer(enabled=True))
        assert _QUERY not in str(_end_fields(caplog))


class TestHowTheTurnEnded:
    async def test_a_model_failure_is_already_logged_so_the_end_is_info(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        result = _result(
            success=False, error="LLM call failed: provider returned 401",
            end_event="llm_call_failed", error_class="misconfigured",
        )
        await _run(StageTimer(enabled=True), make_agent=_ending_with(result))

        [record] = _end_lines(caplog)
        assert record.levelno == logging.INFO
        fields = log_record_fields(record)
        assert (fields["stop_reason"], fields["error_class"], fields["end_event"]) == (
            "error", "misconfigured", "llm_call_failed",
        )
        assert "code" in fields
        assert re.search(r"llm_turn_1=\d+ms\(open\)", fields["spans"])
        assert _warnings(caplog) == []

    async def test_running_out_of_turns_is_a_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        result = _result(output="best effort", end_event="agent_complete_partial")
        await _run(None, make_agent=_ending_with(result))

        [record] = _end_lines(caplog)
        assert (record.levelno, log_record_fields(record)["stop_reason"]) == (logging.WARNING, "max_turns")

    async def test_a_turn_parked_on_an_approval_is_awaiting_input(self, caplog: pytest.LogCaptureFixture) -> None:
        def park(context: AgentContext) -> None:
            context.tool_state[_PENDING_IN_RUN] = "approval-1"

        await _run(None, make_agent=_ending_with(_result(end_event="task_complete"), before=park))

        assert _end_fields(caplog)["stop_reason"] == "awaiting_input"

    async def test_a_clarifying_question_before_the_run_is_awaiting_input(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        await _run(None, clarifying_questions=[MagicMock()])

        fields = _end_fields(caplog)
        assert (fields["stop_reason"], fields["turns"]) == ("awaiting_input", 0)

    async def test_an_already_answered_card_is_done(self, caplog: pytest.LogCaptureFixture) -> None:
        with (
            patch("app.agents.agent_loop.stream_bridge.card_already_answered", return_value=True),
            patch("app.agents.agent_loop.stream_bridge.emit_already_answered", new=AsyncMock()),
        ):
            await _run(None)

        fields = _end_fields(caplog)
        assert (fields["stop_reason"], fields["turns"]) == ("done", 0)

    async def test_a_crash_is_one_failure_line_and_one_end(self, caplog: pytest.LogCaptureFixture) -> None:
        async def _failing_finalizer(self, **kwargs) -> None:
            raise RuntimeError("boom")

        with patch("app.agents.agent_loop.stream_bridge.AnswerFinalizer.run", new=_failing_finalizer):
            chunks = await _run(StageTimer(enabled=True), patch_finalizer=False)

        assert chunks[-1].startswith("event: error")
        [failed] = _warnings(caplog)
        assert (failed.getMessage(), failed.levelno) == ("agent_turn.failed", logging.ERROR)
        assert isinstance(failed.exc_info[1], RuntimeError)
        fields = _end_fields(caplog)
        assert (fields["stop_reason"], fields["error_class"]) == ("error", "internal")

    async def test_a_build_failure_before_the_stream_logs_one_end(self, caplog: pytest.LogCaptureFixture) -> None:
        chunks = await _build_failure_stream()

        assert len(chunks) == 1
        fields = _end_fields(caplog)
        assert (fields["stop_reason"], fields["run_id"], fields["error_class"]) == ("error", _RUN_ID, "internal")
        assert "code" in fields


async def _build_failure_stream() -> list[str]:
    with (
        patch("app.modules.agents.qna.chat_state.build_initial_state", side_effect=TypeError("bad state")),
        patch("app.utils.connector_instances.fetch_user_connector_instances", new=AsyncMock(return_value=[])),
    ):
        return [c async for c in run_agent_loop_stream(
            query_info={"query": _QUERY, "chatMode": "quick", "runId": _RUN_ID},
            user_info={"userId": "user-1", "orgId": "org-1"},
            llm=MagicMock(), log=_LOG, retrieval_service=MagicMock(), graph_provider=MagicMock(),
            reranker_service=MagicMock(), config_service=MagicMock(), model_name="gpt-4",
        )]


class TestTheClientGoingAway:
    async def test_a_disconnect_mid_tool_logs_once_with_the_open_tool(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        await _run(StageTimer(enabled=True), make_agent=_hanging_tool_agent, disconnect_after_first_frame=True)

        fields = _end_fields(caplog)
        assert (fields["stop_reason"], fields["tool_calls"]) == ("client_disconnect", 1)
        assert re.search(r"llm_turn_1=\d+ms(?!\()", fields["spans"])
        assert re.search(r"tool:retrieval=\d+ms\(open\)", fields["spans"])

    async def test_a_disconnect_before_the_stream_starts_logs_once(self, caplog: pytest.LogCaptureFixture) -> None:
        started = asyncio.Event()

        async def _slow_fetch(*args: object) -> list[Any]:
            started.set()
            await asyncio.sleep(30)
            return []

        with patch("app.utils.connector_instances.fetch_user_connector_instances", new=_slow_fetch):
            stream = run_agent_loop_stream(
                query_info={"query": _QUERY, "chatMode": "quick", "runId": _RUN_ID},
                user_info={"userId": "user-1", "orgId": "org-1"},
                llm=MagicMock(), log=_LOG, retrieval_service=MagicMock(), graph_provider=MagicMock(),
                reranker_service=MagicMock(), config_service=MagicMock(), model_name="gpt-4",
            )
            pull = asyncio.ensure_future(stream.__anext__())
            await started.wait()
            pull.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pull

        fields = _end_fields(caplog)
        assert (fields["stop_reason"], fields["run_id"]) == ("client_disconnect", _RUN_ID)

    async def test_a_frame_that_cannot_be_written_is_an_error_not_a_disconnect(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        def make(context: AgentContext) -> MagicMock:
            agent = MagicMock()

            async def _fake_stream(goal: object, **kwargs: object) -> AsyncIterator[AgentEvent]:
                await context.event_sink.write({"event": "status", "data": object()})
                await asyncio.sleep(30)
                return
                yield  # pragma: no cover - marks this an async generator

            agent.stream = _fake_stream
            return agent

        with pytest.raises(TypeError):
            await _run(None, make_agent=make)

        fields = _end_fields(caplog)
        assert (fields["stop_reason"], fields["error_class"]) == ("error", "internal")
        assert [r.getMessage() for r in _warnings(caplog)] == ["agent_turn.failed"]

    async def test_a_logging_failure_still_ends_the_stream(self, caplog: pytest.LogCaptureFixture) -> None:
        with patch("app.agents.agent_loop.turn_log.log_event", side_effect=RuntimeError("log bug")):
            chunks = await asyncio.wait_for(_run(None), timeout=5)

        assert chunks[-1].startswith("event: complete")
        assert [r.getMessage() for r in caplog.records if r.name == "app.agents.agent_loop.turn_log"] == [
            "agent_turn.end failed",
        ]


class TestADisconnectStopsTheModelCall:
    """A real `Agent` whose main model call (the real `UsageReportingTransport` over the real
    `LangChainTransport`) is still streaming when the client goes away. An intent call already
    finished. The provider read gets exactly one cancel and closes, the finished call stays
    counted, and the cut-off call logs only its DEBUG `llm_call.end`."""

    @staticmethod
    def _agent_mid_model_call(model: _StallingModel) -> Callable[[AgentContext], Any]:
        def make(context: AgentContext) -> Any:  # noqa: ANN401
            context.usage_ledger.add("single_shot:intent", TokenUsage(input_tokens=120, output_tokens=7))
            transport = UsageReportingTransport(
                LangChainTransport(model, cancellation_token=context.cancellation_token), context.usage_ledger.add,
            )
            agent, _ = _build(transport)
            return agent

        return make

    @staticmethod
    def _assert_the_turn_logged_one_end_and_no_late_call(caplog: pytest.LogCaptureFixture) -> None:
        fields = _end_fields(caplog)
        assert (fields["stop_reason"], fields["llm_calls"], fields["tokens_in"], fields["tokens_out"]) == (
            "client_disconnect", 1, 120, 7,
        )
        [call_end] = [r for r in caplog.records if r.getMessage() == "llm_call.end"]
        call = log_record_fields(call_end)
        assert (call_end.levelno, call["source"], call["outcome"], call["error_class"]) == (
            logging.DEBUG, "main", "failure", "cancelled",
        )
        assert [r for r in caplog.records if r.exc_info] == []
        assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING and (
            r.name.startswith(("app.agent", _LOG.name))
        )] == []

    async def test_closing_the_stream_cancels_the_provider_read_before_the_close_returns(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        model = _StallingModel([AIMessageChunk(content="partial")])

        async def close_once_the_model_streams(stream: AsyncGenerator[str, None]) -> list[str]:
            chunks = [await stream.__anext__()]
            await model.stall.started.wait()
            await stream.aclose()
            assert (model.stall.cancels, model.stall.closed) == (1, True)
            return chunks

        before = asyncio.all_tasks()
        await _run(None, make_agent=self._agent_mid_model_call(model), goal=Goal(description="g"),
                   drive=close_once_the_model_streams)

        self._assert_the_turn_logged_one_end_and_no_late_call(caplog)
        assert await pending_since(before) == []

    async def test_a_starlette_cancel_scope_reaches_the_provider_read_once(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Starlette's `StreamingResponse` cancels its task group on `http.disconnect`. anyio
        re-cancels the response task on every loop iteration while it unwinds, and the bridge's
        and `Agent.stream()`'s `gather`s forward each re-cancel down the turn."""
        model = _StallingModel([AIMessageChunk(content="partial")])

        async def cancel_like_starlette(stream: AsyncGenerator[str, None]) -> list[str]:
            async def send() -> None:
                async with contextlib.aclosing(stream):
                    async for _ in stream:
                        pass

            async with anyio.create_task_group() as tg:
                tg.start_soon(send)
                await model.stall.started.wait()
                tg.cancel_scope.cancel()
            return []

        before = asyncio.all_tasks()
        await _run(None, make_agent=self._agent_mid_model_call(model), goal=Goal(description="g"),
                   drive=cancel_like_starlette)
        await model.stall.settle()

        assert (model.stall.cancels, model.stall.closed, model.aclose_called) == (1, True, True)
        self._assert_the_turn_logged_one_end_and_no_late_call(caplog)
        assert await pending_since(before) == []
