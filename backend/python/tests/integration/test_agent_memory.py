"""Integration test for the memory characteristics of a long-running agent
conversation -- runs many sequential turns through `run_chat_stream()`'s
real internals (state building, `AgentContext`, hooks, per-request
`_client_cache`, `context.cleanup()`) with only the outermost boundary
(`PipesHubAgentFactory.create`/`AnswerFinalizer.run`) mocked, and asserts on
the real process RSS delta across the whole conversation.

This is a regression guard for the exact class of leak this project's
memory-optimization work targeted: per-turn state (tool results, cached
toolset clients, artifact registries) that should be released at the end of
each turn via `AgentContext.cleanup()`, not accumulate turn over turn.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.chat_modes.bridge import run_chat_stream
from app.agents.chat_modes.policy import AGENT_POLICY
from app.api.routes.chatbot import truncate_previous_conversations
from app.utils.memory_monitor import get_process_memory_mb

_TURN_COUNT = 30
# Generous: each turn seeds a handful of small result/history dicts and one
# fake client. Old (unbounded-accumulation + unclosed-client) behavior grows
# roughly linearly with turn count; this budget is well above the overhead
# of 30 turns' worth of live (non-leaked) per-turn objects, so the test only
# fails on a genuine leak, not incidental interpreter memory noise.
_MAX_RSS_DELTA_MB = 150.0


def _log() -> logging.Logger:
    log = logging.getLogger("test-agent-memory")
    log.setLevel(logging.CRITICAL)
    return log


def _query_info(*, query: str, previous_conversations: list[dict]) -> dict:
    return {
        "query": query,
        "limit": None,
        "previous_conversations": previous_conversations,
        "filters": {},
        "retrievalMode": None,
        "quickMode": False,
        "chatMode": "agent",
        "timezone": None,
        "currentTime": None,
        "conversationId": "conv-agent-memory",
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


class TestAgentConversationMemory:
    async def test_thirty_turn_conversation_rss_delta_stays_under_budget(self):
        captured_tasks: list[asyncio.Task] = []
        real_create_task = asyncio.create_task

        def _capturing_create_task(coro, *args, **kwargs):
            task = real_create_task(coro, *args, **kwargs)
            captured_tasks.append(task)
            return task

        async def _fake_create(self, context, llm, chat_mode, *, query, model_name=""):
            # Each turn caches one small "client" and appends a handful of
            # tool-call records, mirroring what a real retrieval/tool-calling
            # turn accumulates in `tool_state` before `cleanup()` clears it.
            context.tool_state["_client_cache"] = {("jira", "default", "user-1"): AsyncMock(aclose=AsyncMock())}
            context.tool_state.setdefault("all_tool_results", []).extend(
                {"turn": query, "idx": i, "payload": "x" * 256} for i in range(5)
            )

            agent = MagicMock()
            agent.last_stream_result = MagicMock(success=True, error=None, output=f"answer for {query}")

            async def _fake_stream(goal, **kwargs):
                return
                yield  # pragma: no cover

            agent.stream = _fake_stream
            return agent, MagicMock(), MagicMock(constraints=[]), []

        async def _fake_finalizer_run(self, *, agent_success, agent_error, event_sink, agent_output=None, streamed_answer="", reasoning_turns=None):
            await event_sink.write({"event": "complete", "data": {"answer": agent_output}})
            return {"answer": agent_output}

        history: list[dict] = []
        before = get_process_memory_mb()
        assert before is not None, "psutil must be available for this test to be meaningful"

        with (
            patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=_fake_create),
            patch("app.agents.chat_modes.bridge.AnswerFinalizer.run", new=_fake_finalizer_run),
            patch("app.agents.chat_modes.bridge.asyncio.create_task", side_effect=_capturing_create_task),
        ):
            for turn in range(_TURN_COUNT):
                query = f"turn-{turn}: what is the status of ticket {turn}?"
                # Mirrors the real route: history capped BEFORE being handed
                # to `run_chat_stream()`, which does no truncation itself.
                capped_history = truncate_previous_conversations(history)

                async for _ in run_chat_stream(
                    _query_info(query=query, previous_conversations=capped_history),
                    _user_info(), MagicMock(), AGENT_POLICY, _log(),
                    retrieval_service=_retrieval_service(), graph_provider=_graph_provider(),
                    reranker_service=None, config_service=_config_service(),
                ):
                    pass

                history.append({"role": "user", "content": query})
                history.append({"role": "assistant", "content": f"answer for {query}"})

            # Each turn's producer cleanup tail runs in the background on
            # normal exit -- await them all so the RSS sample below reflects
            # steady state, not 30 half-finished cleanups.
            await asyncio.gather(*captured_tasks)

        after = get_process_memory_mb()
        assert after is not None

        delta_rss_mb = after[0] - before[0]
        assert delta_rss_mb < _MAX_RSS_DELTA_MB, (
            f"{_TURN_COUNT}-turn agent conversation grew RSS by "
            f"{delta_rss_mb:.1f} MB, exceeding the {_MAX_RSS_DELTA_MB} MB "
            "budget -- per-turn state is likely not being released"
        )
