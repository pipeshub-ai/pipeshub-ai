"""Integration test for running many chat streams concurrently through
`run_chat_stream()`'s real internals -- a regression guard that concurrent
requests neither share mutable per-request state (each `AgentContext` /
`tool_state` / `_client_cache` must be independent) nor blow up process RSS
proportionally to concurrency (each stream's cached client and tool results
must actually be released via `context.cleanup()`, not just isolated).
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.chat_modes.bridge import run_chat_stream
from app.agents.chat_modes.policy import AGENT_POLICY
from app.utils.memory_monitor import get_process_memory_mb

_STREAM_COUNT = 10
# Generous: 10 concurrent streams, each caching one small client + a few
# small tool-result dicts, should not come close to this even before
# `cleanup()` runs on every one of them.
_MAX_RSS_DELTA_MB = 150.0


def _log() -> logging.Logger:
    log = logging.getLogger("test-concurrent-streams")
    log.setLevel(logging.CRITICAL)
    return log


def _query_info(*, marker: str) -> dict:
    return {
        "query": f"concurrent query for {marker}",
        "limit": None,
        "previous_conversations": [],
        "filters": {},
        "retrievalMode": None,
        "quickMode": False,
        "chatMode": "agent",
        "timezone": None,
        "currentTime": None,
        "conversationId": f"conv-{marker}",
        "attachments": None,
    }


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


class TestConcurrentStreamsCompleteWithoutMemoryBlowup:
    async def test_ten_concurrent_streams_all_complete_and_stay_isolated(self):
        captured_tasks: list[asyncio.Task] = []
        real_create_task = asyncio.create_task

        def _capturing_create_task(coro, *args, **kwargs):
            task = real_create_task(coro, *args, **kwargs)
            captured_tasks.append(task)
            return task

        seen_client_caches: list[dict] = []

        # A single patch shared by every concurrent request, dispatching on
        # the request's own query text -- see
        # `test_query_service_memory_e2e.py::TestConcurrentStreamsDoNotShareState`
        # for why per-coroutine `patch(...)` context managers on the same
        # class attribute is a real race, not a theoretical one.
        async def _fake_create(self, context, llm, chat_mode, *, query, model_name=""):
            marker = query.rsplit(" ", 1)[-1]
            client_cache = {marker: AsyncMock(aclose=AsyncMock())}
            context.tool_state["_client_cache"] = client_cache
            context.tool_state.setdefault("final_results", []).append({"marker": marker})
            seen_client_caches.append(client_cache)

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

        async def _drain_one(marker: str) -> list[str]:
            events = []
            async for event in run_chat_stream(
                _query_info(marker=marker), _user_info(), MagicMock(), AGENT_POLICY, _log(),
                retrieval_service=_retrieval_service(), graph_provider=_graph_provider(),
                reranker_service=None, config_service=_config_service(),
            ):
                events.append(event)
            return events

        before = get_process_memory_mb()
        assert before is not None, "psutil must be available for this test to be meaningful"

        with (
            patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=_fake_create),
            patch("app.agents.chat_modes.bridge.AnswerFinalizer.run", new=_fake_finalizer_run),
            patch("app.agents.chat_modes.bridge.asyncio.create_task", side_effect=_capturing_create_task),
        ):
            markers = [f"stream{i}" for i in range(_STREAM_COUNT)]
            results = await asyncio.gather(*(_drain_one(m) for m in markers))
            await asyncio.gather(*captured_tasks)

        after = get_process_memory_mb()
        assert after is not None

        assert len(results) == _STREAM_COUNT
        for marker, events in zip(markers, results):
            assert any("complete" in e for e in events), f"stream {marker} never completed"

        # Every request got its own `_client_cache` dict -- no two entries
        # share the same object identity.
        assert len({id(cache) for cache in seen_client_caches}) == _STREAM_COUNT

        delta_rss_mb = after[0] - before[0]
        assert delta_rss_mb < _MAX_RSS_DELTA_MB, (
            f"{_STREAM_COUNT} concurrent streams grew RSS by "
            f"{delta_rss_mb:.1f} MB, exceeding the {_MAX_RSS_DELTA_MB} MB "
            "budget -- per-stream state is likely not being released"
        )
