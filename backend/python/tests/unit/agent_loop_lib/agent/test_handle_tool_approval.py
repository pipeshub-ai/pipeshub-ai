"""`agent/observability.py::handle_tool_approval` — the HIL checkpoint/
suspend path `ToolExecutor.call_tool()`'s `on_ask` callback (see
`agent/tool_loop.py`) delegates to for `PreDecision.ASK`.

Mirrors `handle_clarify`'s existing submit -> checkpoint -> wait pattern,
just for a yes/no approval instead of an open question.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.agent_loop_lib.agent.observability import handle_tool_approval
from app.agent_loop_lib.core.types import Goal, ToolCall
from app.agent_loop_lib.modules.stores.hil.base import HILResponse
from app.agent_loop_lib.modules.stores.hil.in_memory import InMemoryHILStore


def _fake_agent(*, hil_store, checkpoint_store=None):
    runtime = SimpleNamespace(hil_store=hil_store, checkpoint_store=checkpoint_store, budget=None)
    run_ctx = SimpleNamespace(run_id="run-1", agent_id="agent-1", parent_run_id=None, trace_id="trace-1", spawn_depth=0)

    async def _emit(event_type, payload):
        pass

    return SimpleNamespace(
        runtime=runtime,
        run_ctx=run_ctx,
        session_id="session-1",
        emit=_emit,
    )


def _goal() -> Goal:
    return Goal(description="do the risky thing")


class TestHandleToolApproval:
    @pytest.mark.asyncio
    async def test_no_hil_store_denies_immediately(self) -> None:
        agent = _fake_agent(hil_store=None)
        call = ToolCall(id="c1", name="delete_everything", arguments={})

        approved = await handle_tool_approval(agent, call, "risky", _goal(), [], 0)

        assert approved is False

    @pytest.mark.asyncio
    async def test_approved_response_unblocks_with_true(self) -> None:
        hil_store = InMemoryHILStore()
        agent = _fake_agent(hil_store=hil_store)
        call = ToolCall(id="c1", name="delete_everything", arguments={})

        async def _respond_once_submitted() -> None:
            while not (await hil_store.list_pending()):
                await asyncio.sleep(0)
            [pending] = await hil_store.list_pending()
            await hil_store.respond(HILResponse(request_id=pending.request_id, approved=True))

        approved, _ = await asyncio.gather(
            handle_tool_approval(agent, call, "risky", _goal(), [], 0),
            _respond_once_submitted(),
        )

        assert approved is True

    @pytest.mark.asyncio
    async def test_denied_response_unblocks_with_false(self) -> None:
        hil_store = InMemoryHILStore()
        agent = _fake_agent(hil_store=hil_store)
        call = ToolCall(id="c1", name="delete_everything", arguments={})

        async def _respond_once_submitted() -> None:
            while not (await hil_store.list_pending()):
                await asyncio.sleep(0)
            [pending] = await hil_store.list_pending()
            await hil_store.respond(HILResponse(request_id=pending.request_id, approved=False))

        approved, _ = await asyncio.gather(
            handle_tool_approval(agent, call, "risky", _goal(), [], 0),
            _respond_once_submitted(),
        )

        assert approved is False

    @pytest.mark.asyncio
    async def test_submits_a_tool_approval_request_with_call_context(self) -> None:
        hil_store = InMemoryHILStore()
        agent = _fake_agent(hil_store=hil_store)
        call = ToolCall(id="c1", name="delete_everything", arguments={"path": "/"})

        async def _respond_once_submitted() -> None:
            while not (await hil_store.list_pending()):
                await asyncio.sleep(0)
            [pending] = await hil_store.list_pending()
            assert pending.context["tool"] == "delete_everything"
            assert pending.context["arguments"] == {"path": "/"}
            assert pending.context["reason"] == "risky"
            await hil_store.respond(HILResponse(request_id=pending.request_id, approved=True))

        await asyncio.gather(
            handle_tool_approval(agent, call, "risky", _goal(), [], 0),
            _respond_once_submitted(),
        )
