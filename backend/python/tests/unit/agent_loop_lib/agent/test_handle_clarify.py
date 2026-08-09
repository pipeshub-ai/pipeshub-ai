"""`agent/observability.py::handle_clarify` — the HIL checkpoint/suspend
path for the library's own `clarify` tool special-route (see `agent/
tool_loop.py`). Mirrors `handle_tool_approval`'s submit -> checkpoint ->
wait pattern for an open question instead of a yes/no decision.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.agent_loop_lib.agent.observability import handle_clarify
from app.agent_loop_lib.core.types import Goal, ToolCall
from app.agent_loop_lib.modules.stores.hil import base as hil_base
from app.agent_loop_lib.modules.stores.hil.base import HILResponse
from app.agent_loop_lib.modules.stores.hil.in_memory import InMemoryHILStore


def _fake_agent(*, hil_store, checkpoint_store=None) -> SimpleNamespace:
    runtime = SimpleNamespace(hil_store=hil_store, checkpoint_store=checkpoint_store, budget=None)
    run_ctx = SimpleNamespace(run_id="run-1", agent_id="agent-1", parent_run_id=None, trace_id="trace-1", spawn_depth=0)

    async def _emit(event_type: object, payload: object) -> None:
        pass

    return SimpleNamespace(runtime=runtime, run_ctx=run_ctx, session_id="session-1", emit=_emit)


def _goal() -> Goal:
    return Goal(description="find out what the user wants")


class TestHandleClarify:
    @pytest.mark.asyncio
    async def test_no_hil_store_errors_immediately(self) -> None:
        agent = _fake_agent(hil_store=None)
        call = ToolCall(id="c1", name="clarify", arguments={"question": "which sprint?"})

        result = await handle_clarify(agent, call, _goal(), [], 0)

        assert result.is_error is True

    @pytest.mark.asyncio
    async def test_answer_unblocks_with_content(self) -> None:
        hil_store = InMemoryHILStore()
        agent = _fake_agent(hil_store=hil_store)
        call = ToolCall(id="c1", name="clarify", arguments={"question": "which sprint?"})

        async def _respond_once_submitted() -> None:
            while not (await hil_store.list_pending()):  # noqa: ASYNC110 -- polling an in-memory fake
                await asyncio.sleep(0)
            [pending] = await hil_store.list_pending()
            await hil_store.respond(HILResponse(request_id=pending.request_id, approved=True, answer="Sprint 42"))

        result, _ = await asyncio.gather(
            handle_clarify(agent, call, _goal(), [], 0),
            _respond_once_submitted(),
        )

        assert result.is_error is False
        assert result.content["answer"] == "Sprint 42"

    @pytest.mark.asyncio
    async def test_unanswered_request_errors_after_timeout_instead_of_hanging_forever(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Task engine plan Part D2 ("TTL on pending questions")."""
        monkeypatch.setattr(hil_base, "DEFAULT_HIL_RESPONSE_TIMEOUT_SECONDS", 0.05)
        hil_store = InMemoryHILStore()
        agent = _fake_agent(hil_store=hil_store)
        call = ToolCall(id="c1", name="clarify", arguments={"question": "which sprint?"})

        result = await asyncio.wait_for(handle_clarify(agent, call, _goal(), [], 0), timeout=2)

        assert result.is_error is True
        assert "expired" in str(result.content).lower()
