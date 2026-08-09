"""`RequestReviewTool` — the probabilistic escalation-for-review tool.

Covers its two already-documented fallback semantics ("no hil_store ->
proceed automatically") plus the timeout fallback added by the task
engine plan's Part D2 ("TTL on pending questions"), which this tool
deliberately treats the SAME as having no `hil_store` at all — both mean
"escalation isn't actually available right now."
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.agent_loop_lib.core.types import Goal, ToolCall
from app.agent_loop_lib.modules.stores.hil import base as hil_base
from app.agent_loop_lib.modules.stores.hil.base import HILResponse
from app.agent_loop_lib.modules.stores.hil.in_memory import InMemoryHILStore
from app.agent_loop_lib.tools.builtin.planning.request_review import RequestReviewTool
from app.agent_loop_lib.tools.special_route import RouteContext


def _ctx(*, hil_store, checkpoint_store=None, goal_description="ship the feature") -> RouteContext:
    runtime = SimpleNamespace(hil_store=hil_store, checkpoint_store=checkpoint_store, budget=None)
    goal = Goal(description=goal_description)
    identity = SimpleNamespace(run_id="run-1", agent_id="agent-1", parent_run_id=None, trace_id="trace-1", spawn_depth=0)

    async def _emit(event_type: object, payload: object) -> None:
        pass

    agent = SimpleNamespace(runtime=runtime, run_ctx=identity, session_id="session-1", emit=_emit)
    run = SimpleNamespace(
        runtime=runtime, spec=None, goal=goal, started_at=None, identity=identity,
        session_id="session-1", todos=[], visible_tools=None,
    )
    turn = SimpleNamespace(run=run, turn_index=0)
    scope = SimpleNamespace(turn=turn, messages=[])
    return RouteContext(agent=agent, scope=scope)


def _call() -> ToolCall:
    return ToolCall(id="c1", name="request_review", arguments={"question": "Is this plan safe to run?"})


class TestRequestReviewTool:
    @pytest.mark.asyncio
    async def test_no_hil_store_proceeds_automatically(self) -> None:
        ctx = _ctx(hil_store=None)

        result = await RequestReviewTool().handle(_call(), ctx)

        assert result.content["approved"] is True

    @pytest.mark.asyncio
    async def test_approved_response_is_honored(self) -> None:
        hil_store = InMemoryHILStore()
        ctx = _ctx(hil_store=hil_store)

        async def _respond_once_submitted() -> None:
            while not (await hil_store.list_pending()):  # noqa: ASYNC110 -- polling an in-memory fake
                await asyncio.sleep(0)
            [pending] = await hil_store.list_pending()
            await hil_store.respond(HILResponse(request_id=pending.request_id, approved=True, answer="looks good"))

        result, _ = await asyncio.gather(
            RequestReviewTool().handle(_call(), ctx),
            _respond_once_submitted(),
        )

        assert result.content == {"approved": True, "reason": "looks good"}

    @pytest.mark.asyncio
    async def test_denied_response_is_honored(self) -> None:
        hil_store = InMemoryHILStore()
        ctx = _ctx(hil_store=hil_store)

        async def _reject_once_submitted() -> None:
            while not (await hil_store.list_pending()):  # noqa: ASYNC110 -- polling an in-memory fake
                await asyncio.sleep(0)
            [pending] = await hil_store.list_pending()
            await hil_store.respond(HILResponse(request_id=pending.request_id, approved=False, answer="too risky"))

        result, _ = await asyncio.gather(
            RequestReviewTool().handle(_call(), ctx),
            _reject_once_submitted(),
        )

        assert result.content == {"approved": False, "reason": "too risky"}

    @pytest.mark.asyncio
    async def test_unanswered_request_proceeds_after_timeout_instead_of_hanging_forever(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(hil_base, "DEFAULT_HIL_RESPONSE_TIMEOUT_SECONDS", 0.05)
        hil_store = InMemoryHILStore()
        ctx = _ctx(hil_store=hil_store)

        result = await asyncio.wait_for(RequestReviewTool().handle(_call(), ctx), timeout=2)

        assert result.content["approved"] is True
        assert "timed out" in result.content["reason"]
