"""Integration tests for the query service's memory-safety behaviors added
for OOM mitigation, exercised through `run_chat_stream()`'s real internals
(`build_initial_state`, `AgentContext`, hooks, the producer/consumer SSE
plumbing) with only the outermost boundary
(`PipesHubAgentFactory.create`/`prefetch_retrieval`) mocked -- the same
seam `tests/integration/test_chat_stream_agent_loop_e2e.py` uses.

Scenarios:
1. Cached toolset clients are closed once a stream completes normally.
2. A client disconnect mid-run cancels the in-flight prefetch AND still
   closes cached clients (no leaked HTTP/OAuth clients on abnormal exit).
3. Two concurrent streams never share `AgentContext.tool_state`/
   `_client_cache` -- a regression guard for the per-request cache/cleanup
   work not accidentally introducing shared mutable state.
4. `previousConversations` truncation applied at the route layer actually
   reaches `AgentContext.previous_conversations` for the agent loop to see.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.agent_loop.context import AgentContext
from app.agents.chat_modes.bridge import run_chat_stream
from app.agents.chat_modes.policy import AGENT_POLICY, INTERNAL_SEARCH_POLICY


def _log() -> logging.Logger:
    log = logging.getLogger("test-query-service-memory-e2e")
    log.setLevel(logging.CRITICAL)
    return log


def _query_info(**overrides) -> dict:
    base = {
        "query": "What is our refund policy?",
        "limit": None,
        "previous_conversations": [],
        "filters": {},
        "retrievalMode": None,
        "quickMode": False,
        "chatMode": "internal_search",
        "timezone": None,
        "currentTime": None,
        "conversationId": "conv-1",
        "attachments": None,
    }
    base.update(overrides)
    return base


def _user_info() -> dict:
    return {"userId": "user-1", "orgId": "org-1", "userEmail": "user@corp.example", "sendUserInfo": True}


def _retrieval_service() -> AsyncMock:
    svc = AsyncMock()
    svc.search_with_filters.return_value = {
        "status_code": 200, "searchResults": [], "virtual_to_record_map": {},
    }
    return svc


def _graph_provider() -> AsyncMock:
    gp = AsyncMock()
    gp.get_user_by_user_id = AsyncMock(return_value=None)
    gp.get_knowledge_hub_filter_options = AsyncMock(return_value={"apps": []})
    return gp


def _config_service() -> AsyncMock:
    cs = AsyncMock()
    cs.get_config = AsyncMock(return_value={"providers": []})
    return cs


@pytest.fixture(autouse=True)
def _no_connectors():
    with (
        patch("app.utils.execute_query.has_sql_connector_configured", new=AsyncMock(return_value=False)),
        patch("app.utils.fetch_slack_thread.has_slack_connector_configured", new=AsyncMock(return_value=False)),
    ):
        yield


def _capturing_create_task():
    """Patch `asyncio.create_task` at the `bridge` module so the test can
    explicitly await the producer's (and, for `agui`, the heartbeat's)
    background task after a normal exit.

    `run_chat_stream()` deliberately does NOT await its producer's cleanup
    tail on a normal exit -- see `bridge.py`'s trailing comment -- so a test
    that just does `async for _ in run_chat_stream(...): pass` leaves that
    task running in the background past the end of the test. Left
    unawaited, it keeps running against a test-scoped event loop that may
    already be closing, leaking warnings/exceptions into whatever test
    happens to run next in the same session. Every test below must gather
    the captured tasks before finishing.
    """
    captured_tasks: list[asyncio.Task] = []
    real_create_task = asyncio.create_task

    def _capturing(coro, *args, **kwargs):
        task = real_create_task(coro, *args, **kwargs)
        captured_tasks.append(task)
        return task

    return captured_tasks, patch("app.agents.chat_modes.bridge.asyncio.create_task", side_effect=_capturing)


def _capture_context():
    """Patch `AgentContext.from_chat_state` to also stash the built
    `AgentContext` in the returned list, so tests can inspect it after the
    stream finishes (the route layer never exposes it directly)."""
    captured: list[AgentContext] = []
    real_from_chat_state = AgentContext.from_chat_state

    def _wrapper(*args, **kwargs):
        context = real_from_chat_state(*args, **kwargs)
        captured.append(context)
        return context

    return captured, patch.object(AgentContext, "from_chat_state", side_effect=_wrapper)


class TestCachedClientsClosedOnNormalCompletion:
    async def test_client_cache_closed_after_stream_drains_to_completion(self):
        """`context.cleanup()` runs in the producer's `finally` block after
        `_DONE` is enqueued but is NOT awaited by the consumer on a normal
        exit (so the HTTP response can close promptly) -- capture the
        background task via `asyncio.create_task` so the test can await it
        deterministically instead of relying on a sleep."""
        fake_client = AsyncMock()
        fake_client.aclose = AsyncMock()

        async def _fake_create(self, context, llm, chat_mode, *, query, model_name=""):
            context.tool_state["_client_cache"] = {("jira", "default", "user-1"): fake_client}

            agent = MagicMock()
            agent.last_stream_result = MagicMock(success=True, error=None, output="Answer.")

            async def _fake_stream(goal, **kwargs):
                return
                yield  # pragma: no cover

            agent.stream = _fake_stream
            return agent, MagicMock(), MagicMock(constraints=[]), []

        async def _fake_finalizer_run(self, *, agent_success, agent_error, event_sink, agent_output=None, streamed_answer="", reasoning_turns=None):
            await event_sink.write({"event": "complete", "data": {"answer": agent_output}})
            return {"answer": agent_output}

        captured_tasks, create_task_patch = _capturing_create_task()

        with (
            patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=_fake_create),
            patch("app.agents.chat_modes.bridge.AnswerFinalizer.run", new=_fake_finalizer_run),
            create_task_patch,
        ):
            events = [
                event async for event in run_chat_stream(
                    _query_info(chatMode="agent"), _user_info(), MagicMock(), AGENT_POLICY, _log(),
                    retrieval_service=_retrieval_service(), graph_provider=_graph_provider(),
                    reranker_service=None, config_service=_config_service(),
                )
            ]
            # Producer's cleanup tail is intentionally not awaited on normal
            # exit -- wait for it here so the assertion below is deterministic.
            await asyncio.gather(*captured_tasks)

        assert any('"event": "complete"' in e or "complete" in e for e in events)
        fake_client.aclose.assert_awaited_once()


class TestDisconnectCancelsPrefetchAndCleansUp:
    async def test_aclose_mid_run_cancels_prefetch_and_closes_cached_clients(self):
        """A client disconnect (caller stops iterating and closes the
        generator) while `factory.create()` is still in flight must: (1)
        cancel the concurrently-running prefetch task rather than letting
        it keep doing retrieval work nothing will read, and (2) still run
        `context.cleanup()` so any client already cached is closed."""
        prefetch_started = asyncio.Event()
        prefetch_cancelled = False
        create_started = asyncio.Event()
        fake_client = AsyncMock()
        fake_client.aclose = AsyncMock()

        async def _fake_prefetch(**kwargs):
            prefetch_started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                nonlocal prefetch_cancelled
                prefetch_cancelled = True
                raise
            return None

        async def _hanging_create(self, context, llm, chat_mode, *, query, model_name=""):
            context.tool_state["_client_cache"] = {("slack", "default", "user-1"): fake_client}
            create_started.set()
            await asyncio.Event().wait()  # never returns until the producer task is cancelled

        with (
            patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=_hanging_create),
            patch("app.agents.chat_modes.bridge.prefetch_retrieval", new=_fake_prefetch),
        ):
            agen = run_chat_stream(
                _query_info(chatMode="internal_search"), _user_info(), MagicMock(), INTERNAL_SEARCH_POLICY, _log(),
                retrieval_service=_retrieval_service(), graph_provider=_graph_provider(),
                reranker_service=None, config_service=_config_service(),
            )
            consume_task = asyncio.ensure_future(agen.__anext__())
            await asyncio.wait_for(prefetch_started.wait(), timeout=5)
            await asyncio.wait_for(create_started.wait(), timeout=5)

            # Client disconnects: cancelling the task consuming the stream
            # (the same thing FastAPI does when the underlying HTTP
            # connection drops mid-`StreamingResponse`) injects
            # `CancelledError` into `run_chat_stream()`'s own consumer loop
            # while it's suspended on `await queue.get()`, driving its
            # `finally` block the same way a real disconnect would.
            consume_task.cancel()
            try:
                await consume_task
            except (asyncio.CancelledError, StopAsyncIteration):
                pass
            await agen.aclose()

        assert prefetch_cancelled is True
        fake_client.aclose.assert_awaited_once()


class TestConcurrentStreamsDoNotShareState:
    async def test_two_concurrent_streams_have_independent_tool_state_and_client_caches(self):
        """`_client_cache` lives on each request's own `tool_state` dict
        (seeded fresh per `AgentContext`) -- verify two requests running at
        the same time never observe each other's cached clients or
        accumulated results."""
        captured, from_chat_state_patch = _capture_context()

        # A SINGLE patch shared by both concurrent requests, dispatching on
        # the request's own `query` text -- two coroutines each opening/
        # closing their OWN `patch(...)` on the same class attribute
        # concurrently is a real `unittest.mock` race (whichever exits
        # second restores what the OTHER coroutine's patch had installed,
        # permanently corrupting the class attribute for every later test
        # in the same session), not just a theoretical concern.
        async def _fake_create(self, context, llm, chat_mode, *, query, model_name=""):
            marker = "req-a" if "req-a" in query else "req-b"
            context.tool_state["_client_cache"] = {marker: AsyncMock()}
            context.tool_state.setdefault("final_results", []).append({"marker": marker})

            agent = MagicMock()
            agent.last_stream_result = MagicMock(success=True, error=None, output=f"answer-{marker}")

            async def _fake_stream(goal, **kwargs):
                await asyncio.sleep(0.01)
                return
                yield  # pragma: no cover

            agent.stream = _fake_stream
            return agent, MagicMock(), MagicMock(constraints=[]), []

        async def _fake_finalizer_run(self, *, agent_success, agent_error, event_sink, agent_output=None, streamed_answer="", reasoning_turns=None):
            await event_sink.write({"event": "complete", "data": {"answer": agent_output}})
            return {"answer": agent_output}

        async def _drain_one(marker: str, captured_tasks: list[asyncio.Task]) -> None:
            async for _ in run_chat_stream(
                _query_info(chatMode="agent", query=f"query for {marker}", conversationId=f"conv-{marker}"),
                _user_info(), MagicMock(), AGENT_POLICY, _log(),
                retrieval_service=_retrieval_service(), graph_provider=_graph_provider(),
                reranker_service=None, config_service=_config_service(),
            ):
                pass
            await asyncio.gather(*captured_tasks)

        captured_tasks, create_task_patch = _capturing_create_task()
        with (
            from_chat_state_patch,
            patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=_fake_create),
            patch("app.agents.chat_modes.bridge.AnswerFinalizer.run", new=_fake_finalizer_run),
            create_task_patch,
        ):
            await asyncio.gather(_drain_one("req-a", captured_tasks), _drain_one("req-b", captured_tasks))

        assert len(captured) == 2
        cache_a = captured[0].tool_state.get("_client_cache") if captured[0].tool_state else None
        cache_b = captured[1].tool_state.get("_client_cache") if captured[1].tool_state else None
        # `cleanup()` clears `tool_state` after each run, so by the time we
        # inspect them both should already be empty -- the point is that
        # they are two DIFFERENT dict objects, not the same shared instance.
        assert captured[0].tool_state is not captured[1].tool_state
        assert cache_a in (None, {})
        assert cache_b in (None, {})


class TestPreviousConversationsTruncationReachesAgentContext:
    async def test_route_level_truncation_is_visible_on_agent_context(self):
        """`truncate_previous_conversations()` runs at the route layer
        before `run_chat_stream()` is ever called -- verify the capped list
        (not the original, unbounded one) is what ends up on
        `AgentContext.previous_conversations` for prompt building to read."""
        from app.api.routes.chatbot import MAX_PREVIOUS_CONVERSATIONS, truncate_previous_conversations

        oversized_history = [{"role": "user", "content": str(i)} for i in range(MAX_PREVIOUS_CONVERSATIONS + 15)]
        capped_history = truncate_previous_conversations(oversized_history)
        assert len(capped_history) == MAX_PREVIOUS_CONVERSATIONS

        snapshot: dict[str, int] = {}

        async def _fake_create(self, context, llm, chat_mode, *, query, model_name=""):
            # Snapshot BEFORE `context.cleanup()` (called from the producer's
            # `finally` block after this run completes) clears
            # `previous_conversations` back to `[]`.
            snapshot["len"] = len(context.previous_conversations)

            agent = MagicMock()
            agent.last_stream_result = MagicMock(success=True, error=None, output="ok")

            async def _fake_stream(goal, **kwargs):
                return
                yield  # pragma: no cover

            agent.stream = _fake_stream
            return agent, MagicMock(), MagicMock(constraints=[]), []

        async def _fake_finalizer_run(self, *, agent_success, agent_error, event_sink, agent_output=None, streamed_answer="", reasoning_turns=None):
            await event_sink.write({"event": "complete", "data": {"answer": agent_output}})
            return {"answer": agent_output}

        captured_tasks, create_task_patch = _capturing_create_task()
        with (
            patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=_fake_create),
            patch("app.agents.chat_modes.bridge.AnswerFinalizer.run", new=_fake_finalizer_run),
            create_task_patch,
        ):
            async for _ in run_chat_stream(
                # Simulates the route layer having already applied
                # `truncate_previous_conversations()` before calling in --
                # `run_chat_stream()` itself does no truncation of its own.
                _query_info(chatMode="agent", previous_conversations=capped_history),
                _user_info(), MagicMock(), AGENT_POLICY, _log(),
                retrieval_service=_retrieval_service(), graph_provider=_graph_provider(),
                reranker_service=None, config_service=_config_service(),
            ):
                pass
            await asyncio.gather(*captured_tasks)

        # The bridge builds `AgentContext` straight from whatever
        # `query_info["previous_conversations"]` it's handed -- it does its
        # own truncation nowhere. If the ROUTE layer's cap didn't run before
        # calling in, the oversized list would reach the context untouched.
        assert snapshot["len"] == MAX_PREVIOUS_CONVERSATIONS
