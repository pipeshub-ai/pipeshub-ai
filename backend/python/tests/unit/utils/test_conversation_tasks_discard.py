"""Run teardown must leave no conversation entry behind, whatever the exit."""

import asyncio

import pytest

from app.utils import conversation_tasks as ct


@pytest.mark.asyncio
async def test_discard_cancels_and_forgets_pending_tasks() -> None:
    task = asyncio.ensure_future(asyncio.sleep(60))
    ct.register_task("conv-discard-1", task)
    assert ct.discard_tasks("conv-discard-1") == 1
    assert "conv-discard-1" not in ct._conversation_tasks
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_discard_after_drain_is_a_noop() -> None:
    assert ct.discard_tasks("conv-never-registered") == 0
    assert ct.discard_tasks(None) == 0


async def _run_stream(conv_id: str, *, disconnect: bool) -> asyncio.Future:
    """Run the agent stream with a factory that registers a task, then fails/stalls."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.agents.agent_loop.stream_bridge import run_agent_loop_stream

    pending = asyncio.ensure_future(asyncio.sleep(60))

    async def _fake_create(self: object, context: object, llm: object, chat_mode: object, **kwargs: object) -> None:
        ct.register_task(conv_id, pending)
        await context.event_sink.write({"event": "status", "data": {"status": "planning"}})
        if disconnect:
            await asyncio.sleep(60)
        raise RuntimeError("boom")

    with (
        patch(
            "app.modules.agents.qna.chat_state.build_initial_state",
            return_value={"org_id": "o", "user_id": "u", "query": "hi"},
        ),
        patch("app.utils.connector_instances.fetch_user_connector_instances", new=AsyncMock(return_value=[])),
        patch("app.agents.agent_loop.stream_bridge.PipesHubAgentFactory.create", new=_fake_create),
    ):
        stream = run_agent_loop_stream(
            query_info={"query": "hi", "chatMode": "quick", "conversationId": conv_id},
            user_info={"userId": "u", "orgId": "o"},
            llm=MagicMock(), log=MagicMock(), retrieval_service=MagicMock(),
            graph_provider=MagicMock(), reranker_service=None,
            config_service=MagicMock(), model_name="gpt-4",
        )
        if disconnect:
            await stream.__anext__()
            await stream.aclose()
        else:
            _ = [chunk async for chunk in stream]
    await asyncio.sleep(0.05)
    return pending


@pytest.mark.asyncio
async def test_failed_run_leaves_no_registry_entry() -> None:
    pending = await _run_stream("conv-err", disconnect=False)
    assert "conv-err" not in ct._conversation_tasks
    assert pending.cancelled()


@pytest.mark.asyncio
async def test_cancelled_run_leaves_no_registry_entry() -> None:
    pending = await _run_stream("conv-cancel", disconnect=True)
    assert "conv-cancel" not in ct._conversation_tasks
    assert pending.cancelled()
