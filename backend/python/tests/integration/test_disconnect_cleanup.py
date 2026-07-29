"""Integration test for the full client-disconnect cleanup sequence in
`run_chat_stream()`'s `finally` block: cancel the orphaned prefetch task,
close cached toolset clients, and destroy any sandbox the run created --
the same 4-step order documented in `stream_bridge.py`/`bridge.py` (flush +
`_DONE`, clear context state, cancel orphan tasks, destroy sandboxes).

`tests/integration/test_query_service_memory_e2e.py::TestDisconnectCancelsPrefetchAndCleansUp`
covers prefetch-cancellation + client-cache-closing for this same scenario;
this file adds the sandbox-teardown leg of that sequence.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.chat_modes.bridge import run_chat_stream
from app.agents.chat_modes.policy import INTERNAL_SEARCH_POLICY


def _log() -> logging.Logger:
    log = logging.getLogger("test-disconnect-cleanup")
    log.setLevel(logging.CRITICAL)
    return log


def _query_info() -> dict:
    return {
        "query": "What is our refund policy?",
        "limit": None,
        "previous_conversations": [],
        "filters": {},
        "retrievalMode": None,
        "quickMode": False,
        "chatMode": "internal_search",
        "timezone": None,
        "currentTime": None,
        "conversationId": "conv-disconnect",
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


@pytest.fixture(autouse=True)
def _no_connectors():
    with (
        patch("app.utils.execute_query.has_sql_connector_configured", new=AsyncMock(return_value=False)),
        patch("app.utils.fetch_slack_thread.has_slack_connector_configured", new=AsyncMock(return_value=False)),
    ):
        yield


class TestClientDisconnectRunsFullCleanupSequence:
    async def test_disconnect_cancels_prefetch_closes_clients_and_destroys_sandbox(self):
        prefetch_started = asyncio.Event()
        prefetch_cancelled = False
        create_started = asyncio.Event()
        fake_client = AsyncMock()
        fake_client.aclose = AsyncMock()
        fake_sandbox_manager = AsyncMock()
        fake_sandbox_manager.destroy_all = AsyncMock()

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
            context.sandbox_manager = fake_sandbox_manager
            create_started.set()
            await asyncio.Event().wait()  # never returns until the producer task is cancelled

        with (
            patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=_hanging_create),
            patch("app.agents.chat_modes.bridge.prefetch_retrieval", new=_fake_prefetch),
        ):
            agen = run_chat_stream(
                _query_info(), _user_info(), MagicMock(), INTERNAL_SEARCH_POLICY, _log(),
                retrieval_service=_retrieval_service(), graph_provider=_graph_provider(),
                reranker_service=None, config_service=_config_service(),
            )
            consume_task = asyncio.ensure_future(agen.__anext__())
            await asyncio.wait_for(prefetch_started.wait(), timeout=5)
            await asyncio.wait_for(create_started.wait(), timeout=5)

            # Simulate the client dropping the HTTP connection mid-stream:
            # cancel the task consuming the generator (what FastAPI does
            # when the underlying connection drops mid-`StreamingResponse`)
            # so `CancelledError` is injected into `run_chat_stream()`'s own
            # consumer loop, driving its `finally` block the same way a real
            # disconnect would.
            consume_task.cancel()
            try:
                await consume_task
            except (asyncio.CancelledError, StopAsyncIteration):
                pass
            await agen.aclose()

        assert prefetch_cancelled is True, "prefetch task must be cancelled, not left running against a dead connection"
        fake_client.aclose.assert_awaited_once()
        fake_sandbox_manager.destroy_all.assert_awaited_once()
