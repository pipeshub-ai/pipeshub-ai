"""Agent-loop events -> PipesHub SSE event format (`sse_emitter.py`) and the
push-to-pull streaming bridge (`stream_bridge.py`) — Phase 7/8 of the
migration."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.agent_loop_lib.events.base import AgentEvent, EventType, RunContext
from app.agents.agent_loop.sse_emitter import SSEEventEmitter
from app.agents.agent_loop.stream_bridge import QueueEventSink, run_agent_loop_stream

_RUN_CTX = RunContext(role_name="pipeshub-agent", model="gpt-4")


def _event(event_type: EventType, payload: dict[str, Any]) -> AgentEvent:
    return AgentEvent(event_type=event_type, run_context=_RUN_CTX, payload=payload)


class TestQueueEventSink:
    async def test_write_puts_event_on_queue(self) -> None:
        queue: asyncio.Queue = asyncio.Queue()
        sink = QueueEventSink(queue)

        result = await sink.write({"event": "status", "data": {"message": "hi"}})

        assert result is True
        assert queue.qsize() == 1
        assert await queue.get() == {"event": "status", "data": {"message": "hi"}}

    async def test_preserves_fifo_order(self) -> None:
        queue: asyncio.Queue = asyncio.Queue()
        sink = QueueEventSink(queue)

        await sink.write({"event": "a"})
        await sink.write({"event": "b"})

        assert (await queue.get())["event"] == "a"
        assert (await queue.get())["event"] == "b"


class TestSSEEventEmitterTranslation:
    async def test_run_started_maps_to_planning_status(self) -> None:
        sink = MagicMock()
        sink.write = AsyncMock(return_value=True)
        emitter = SSEEventEmitter(sink)

        await emitter.emit(_event(EventType.RUN_STARTED, {}))

        sink.write.assert_awaited_once_with(
            {"event": "status", "data": {"status": "planning", "message": "Planning how to answer..."}}
        )

    async def test_tool_call_start_maps_to_executing_status(self) -> None:
        sink = MagicMock()
        sink.write = AsyncMock(return_value=True)
        emitter = SSEEventEmitter(sink)

        await emitter.emit(_event(EventType.TOOL_CALL_START, {"tool": "jira_search"}))

        sink.write.assert_awaited_once_with(
            {"event": "status", "data": {"status": "executing", "message": "Using jira_search..."}}
        )

    async def test_tool_call_end_success_maps_to_tool_result(self) -> None:
        sink = MagicMock()
        sink.write = AsyncMock(return_value=True)
        emitter = SSEEventEmitter(sink)

        await emitter.emit(_event(EventType.TOOL_CALL_END, {"tool": "jira_search", "content": "3 issues found"}))

        sink.write.assert_awaited_once_with(
            {"event": "tool_result", "data": {"tool": "jira_search", "result": "3 issues found", "status": "success"}}
        )

    async def test_tool_call_end_error_maps_to_error_status(self) -> None:
        sink = MagicMock()
        sink.write = AsyncMock(return_value=True)
        emitter = SSEEventEmitter(sink)

        await emitter.emit(
            _event(EventType.TOOL_CALL_END, {"tool": "jira_search", "content": None, "is_error": True})
        )

        sink.write.assert_awaited_once_with(
            {"event": "tool_result", "data": {"tool": "jira_search", "result": None, "status": "error"}}
        )

    async def test_tool_blocked_alias_maps_to_error_tool_result(self) -> None:
        """`TOOL_CALL_END` aliased from `TOOL_BLOCKED` carries `reason` but
        no `is_error` key — see `sse_emitter.py`'s module docstring."""
        sink = MagicMock()
        sink.write = AsyncMock(return_value=True)
        emitter = SSEEventEmitter(sink)

        await emitter.emit(
            _event(EventType.TOOL_CALL_END, {"tool": "jira_search", "reason": "blocked after 3 failures"})
        )

        sink.write.assert_awaited_once_with(
            {"event": "tool_result", "data": {"tool": "jira_search", "result": "blocked after 3 failures", "status": "error"}}
        )

    async def test_unmapped_event_types_are_silently_dropped(self) -> None:
        sink = MagicMock()
        sink.write = AsyncMock(return_value=True)
        emitter = SSEEventEmitter(sink)

        await emitter.emit(_event(EventType.RUN_FINISHED, {}))
        await emitter.emit(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "hi"}))

        sink.write.assert_not_awaited()


class TestRunAgentLoopStream:
    @staticmethod
    def _base_kwargs() -> dict[str, Any]:
        return {
            "query_info": {"query": "hello", "chatMode": "react"},
            "user_info": {"userId": "user-1", "orgId": "org-1"},
            "llm": MagicMock(),
            "log": MagicMock(),
            "retrieval_service": MagicMock(),
            "graph_provider": MagicMock(),
            "reranker_service": MagicMock(),
            "config_service": MagicMock(),
        }

    async def test_build_initial_state_failure_yields_error_event(self) -> None:
        with (
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                side_effect=RuntimeError("boom"),
            ),
            patch(
                "app.utils.execute_query.has_sql_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.utils.fetch_slack_thread.has_slack_connector_configured",
                new=AsyncMock(return_value=False),
            ),
        ):
            events = [chunk async for chunk in run_agent_loop_stream(**self._base_kwargs())]

        assert len(events) == 1
        assert events[0].startswith("event: error\n")
        # Raw exception text is classified (`error_classification.py`) into a
        # stable `type` + friendly `message` rather than leaked verbatim.
        payload = json.loads(events[0].split("data: ", 1)[1].strip())
        assert payload["type"] == "unknown"
        assert "boom" not in payload["message"]

    async def test_successful_run_streams_events_then_completes(self) -> None:
        async def _fake_create(self, context, llm, chat_mode, *, query, model_name="", session_id=None):
            await context.event_sink.write({"event": "status", "data": {"status": "planning", "message": "..."}})
            agent = MagicMock()
            agent.run = AsyncMock(return_value=MagicMock(success=True, error=None))
            return agent, MagicMock()

        async def _fake_respond_run(self, *, agent_success, agent_error, event_sink):
            await event_sink.write({"event": "complete", "data": {"answer": "42"}})
            return {"answer": "42"}

        with (
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "hello"},
            ),
            patch(
                "app.utils.execute_query.has_sql_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.utils.fetch_slack_thread.has_slack_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.agents.agent_loop.stream_bridge.PipesHubAgentFactory.create",
                new=_fake_create,
            ),
            patch(
                "app.agents.agent_loop.stream_bridge.RespondPipeline.run",
                new=_fake_respond_run,
            ),
        ):
            events = [chunk async for chunk in run_agent_loop_stream(**self._base_kwargs())]

        assert len(events) == 2
        assert events[0].startswith("event: status\n")
        assert events[1].startswith("event: complete\n")
        assert json.loads(events[1].split("data: ", 1)[1].strip()) == {"answer": "42"}

    async def test_agent_run_failure_emits_error_event(self) -> None:
        async def _fake_create(self, context, llm, chat_mode, *, query, model_name="", session_id=None):
            raise RuntimeError("transport exploded")

        with (
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "hello"},
            ),
            patch(
                "app.utils.execute_query.has_sql_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.utils.fetch_slack_thread.has_slack_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.agents.agent_loop.stream_bridge.PipesHubAgentFactory.create",
                new=_fake_create,
            ),
        ):
            events = [chunk async for chunk in run_agent_loop_stream(**self._base_kwargs())]

        assert len(events) == 1
        assert events[0].startswith("event: error\n")
        payload = json.loads(events[0].split("data: ", 1)[1].strip())
        assert payload["type"] == "unknown"
        assert "transport exploded" not in payload["message"]

    async def test_agent_run_failure_classifies_rate_limit_error(self) -> None:
        """A 429 raised anywhere in `_produce()` must surface with
        `type: "rate_limit"` and a friendly message — see
        `error_classification.py`."""
        async def _fake_create(self, context, llm, chat_mode, *, query, model_name="", session_id=None):
            raise RuntimeError("Error code: 429 - rate limit exceeded")

        with (
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "hello"},
            ),
            patch(
                "app.utils.execute_query.has_sql_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.utils.fetch_slack_thread.has_slack_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.agents.agent_loop.stream_bridge.PipesHubAgentFactory.create",
                new=_fake_create,
            ),
        ):
            events = [chunk async for chunk in run_agent_loop_stream(**self._base_kwargs())]

        assert len(events) == 1
        payload = json.loads(events[0].split("data: ", 1)[1].strip())
        assert payload["type"] == "rate_limit"
        assert payload["message"] == "The AI service is currently rate limited. Please try again in a moment."

    async def test_sandbox_manager_destroyed_on_successful_completion(self) -> None:
        """Phase 8's `_produce()` `finally` block must tear down the
        per-request `SandboxManager` whenever one was stashed on the
        context — normal completion, agent failure, and (separately,
        see below) client-disconnect cancellation alike."""
        sandbox_manager = MagicMock()
        sandbox_manager.destroy_all = AsyncMock()

        async def _fake_create(self, context, llm, chat_mode, *, query, model_name="", session_id=None):
            context.sandbox_manager = sandbox_manager
            agent = MagicMock()
            agent.run = AsyncMock(return_value=MagicMock(success=True, error=None))
            return agent, MagicMock()

        async def _fake_respond_run(self, *, agent_success, agent_error, event_sink):
            await event_sink.write({"event": "complete", "data": {"answer": "42"}})
            return {"answer": "42"}

        with (
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "hello"},
            ),
            patch(
                "app.utils.execute_query.has_sql_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.utils.fetch_slack_thread.has_slack_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.agents.agent_loop.stream_bridge.PipesHubAgentFactory.create",
                new=_fake_create,
            ),
            patch(
                "app.agents.agent_loop.stream_bridge.RespondPipeline.run",
                new=_fake_respond_run,
            ),
        ):
            events = [chunk async for chunk in run_agent_loop_stream(**self._base_kwargs())]

        assert len(events) == 1
        sandbox_manager.destroy_all.assert_awaited_once()

    async def test_sandbox_manager_destroyed_even_when_agent_run_fails(self) -> None:
        sandbox_manager = MagicMock()
        sandbox_manager.destroy_all = AsyncMock()

        async def _fake_create(self, context, llm, chat_mode, *, query, model_name="", session_id=None):
            context.sandbox_manager = sandbox_manager
            raise RuntimeError("boom")

        with (
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "hello"},
            ),
            patch(
                "app.utils.execute_query.has_sql_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.utils.fetch_slack_thread.has_slack_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.agents.agent_loop.stream_bridge.PipesHubAgentFactory.create",
                new=_fake_create,
            ),
        ):
            events = [chunk async for chunk in run_agent_loop_stream(**self._base_kwargs())]

        assert len(events) == 1
        assert events[0].startswith("event: error\n")
        sandbox_manager.destroy_all.assert_awaited_once()

    async def test_sandbox_cleanup_failure_does_not_prevent_stream_completion(self) -> None:
        """A broken `destroy_all()` must be swallowed (logged), not
        propagate and break the SSE stream for the client."""
        sandbox_manager = MagicMock()
        sandbox_manager.destroy_all = AsyncMock(side_effect=RuntimeError("teardown exploded"))

        async def _fake_create(self, context, llm, chat_mode, *, query, model_name="", session_id=None):
            context.sandbox_manager = sandbox_manager
            agent = MagicMock()
            agent.run = AsyncMock(return_value=MagicMock(success=True, error=None))
            return agent, MagicMock()

        async def _fake_respond_run(self, *, agent_success, agent_error, event_sink):
            await event_sink.write({"event": "complete", "data": {"answer": "42"}})
            return {"answer": "42"}

        with (
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "hello"},
            ),
            patch(
                "app.utils.execute_query.has_sql_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.utils.fetch_slack_thread.has_slack_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.agents.agent_loop.stream_bridge.PipesHubAgentFactory.create",
                new=_fake_create,
            ),
            patch(
                "app.agents.agent_loop.stream_bridge.RespondPipeline.run",
                new=_fake_respond_run,
            ),
        ):
            events = [chunk async for chunk in run_agent_loop_stream(**self._base_kwargs())]

        assert len(events) == 1
        assert events[0].startswith("event: complete\n")
        sandbox_manager.destroy_all.assert_awaited_once()

    async def test_no_sandbox_manager_is_a_no_op(self) -> None:
        """When code execution isn't enabled for the request,
        `context.sandbox_manager` stays `None` — the cleanup block must
        skip cleanly rather than erroring on a missing manager."""
        async def _fake_create(self, context, llm, chat_mode, *, query, model_name="", session_id=None):
            assert context.sandbox_manager is None
            agent = MagicMock()
            agent.run = AsyncMock(return_value=MagicMock(success=True, error=None))
            return agent, MagicMock()

        async def _fake_respond_run(self, *, agent_success, agent_error, event_sink):
            await event_sink.write({"event": "complete", "data": {"answer": "42"}})
            return {"answer": "42"}

        with (
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "hello"},
            ),
            patch(
                "app.utils.execute_query.has_sql_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.utils.fetch_slack_thread.has_slack_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.agents.agent_loop.stream_bridge.PipesHubAgentFactory.create",
                new=_fake_create,
            ),
            patch(
                "app.agents.agent_loop.stream_bridge.RespondPipeline.run",
                new=_fake_respond_run,
            ),
        ):
            events = [chunk async for chunk in run_agent_loop_stream(**self._base_kwargs())]

        assert len(events) == 1
        assert events[0].startswith("event: complete\n")
