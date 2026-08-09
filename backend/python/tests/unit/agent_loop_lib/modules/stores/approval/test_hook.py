"""`ApprovalHook.check()` (task engine plan Part D, Phase 5) --
covers the previous "submit-then-deny" bug: a policy requiring a human
decision (ASK_ONCE with no cached decision / ASK_EACH_TIME) must actually
wait for the HIL response, not submit and immediately record a false
denial. Also covers the "fail loudly, not false denial" fallback when no
`hil_store` is configured at all.
"""

from __future__ import annotations

import asyncio

import pytest

from app.agent_loop_lib.core.types import ToolCall
from app.agent_loop_lib.modules.stores.approval.base import (
    ApprovalPolicy,
    RiskLevel,
)
from app.agent_loop_lib.modules.stores.approval.hook import ApprovalHook
from app.agent_loop_lib.modules.stores.approval.in_memory import InMemoryApprovalStore
from app.agent_loop_lib.modules.stores.hil.base import HILResponse
from app.agent_loop_lib.modules.stores.hil.in_memory import InMemoryHILStore


def _call(name: str = "delete_issue") -> ToolCall:
    return ToolCall(id="call-1", name=name, arguments={"key": "PA-1"})


class TestAskPolicyWaitsForHumanResponse:
    async def test_waits_and_returns_approved_true(self) -> None:
        store = InMemoryApprovalStore()
        await store.set_policy("delete_issue", ApprovalPolicy.ASK_EACH_TIME)
        hil_store = InMemoryHILStore()
        hook = ApprovalHook(store, hil_store=hil_store)

        async def _respond_after_submit() -> None:
            # Wait until the request has actually been submitted before
            # responding, so this exercises the real wait path (not a
            # response that was already there before `check()` started).
            for _ in range(100):
                pending = await hil_store.list_pending()
                if pending:
                    await hil_store.respond(HILResponse(request_id=pending[0].request_id, approved=True))
                    return
                await asyncio.sleep(0)
            raise AssertionError("HIL request was never submitted")

        decision, _ = await asyncio.gather(
            hook.check(_call(), session_id="sess-1"),
            _respond_after_submit(),
        )

        assert decision.approved is True
        assert decision.reason is not None and decision.reason.startswith("hil_request_id=")

    async def test_waits_and_returns_approved_false_on_real_rejection(self) -> None:
        store = InMemoryApprovalStore()
        await store.set_policy("delete_issue", ApprovalPolicy.ASK_EACH_TIME)
        hil_store = InMemoryHILStore()
        hook = ApprovalHook(store, hil_store=hil_store)

        async def _reject_after_submit() -> None:
            for _ in range(100):
                pending = await hil_store.list_pending()
                if pending:
                    await hil_store.respond(HILResponse(request_id=pending[0].request_id, approved=False))
                    return
                await asyncio.sleep(0)
            raise AssertionError("HIL request was never submitted")

        decision, _ = await asyncio.gather(
            hook.check(_call(), session_id="sess-1"),
            _reject_after_submit(),
        )

        assert decision.approved is False

    async def test_does_not_return_before_a_response_exists(self) -> None:
        """Regression test for the exact submit-then-deny bug: `check()`
        must not resolve `approved=False` merely because a HIL request was
        submitted -- only once an actual response is available."""
        store = InMemoryApprovalStore()
        await store.set_policy("delete_issue", ApprovalPolicy.ASK_EACH_TIME)
        hil_store = InMemoryHILStore()
        hook = ApprovalHook(store, hil_store=hil_store)

        task = asyncio.ensure_future(hook.check(_call(), session_id="sess-1"))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not task.done(), "check() resolved before any HIL response was recorded"

        pending = await hil_store.list_pending()
        assert len(pending) == 1
        await hil_store.respond(HILResponse(request_id=pending[0].request_id, approved=True))
        decision = await task
        assert decision.approved is True


class TestNoHilStoreFailsLoudly:
    async def test_ask_policy_without_hil_store_raises(self) -> None:
        store = InMemoryApprovalStore()
        await store.set_policy("delete_issue", ApprovalPolicy.ASK_EACH_TIME)
        hook = ApprovalHook(store, hil_store=None)

        with pytest.raises(RuntimeError, match="no hil_store"):
            await hook.check(_call(), session_id="sess-1")

    async def test_auto_approve_policy_still_short_circuits_without_hil_store(self) -> None:
        """AUTO_APPROVE/AUTO_DENY never need a human, so no `hil_store` is
        required for those policies specifically."""
        store = InMemoryApprovalStore()
        await store.set_policy("read_issue", ApprovalPolicy.AUTO_APPROVE)
        hook = ApprovalHook(store, hil_store=None)

        decision = await hook.check(_call(name="read_issue"), session_id="sess-1")

        assert decision.approved is True


class TestSessionCaching:
    async def test_ask_once_reuses_cached_decision_without_asking_again(self) -> None:
        store = InMemoryApprovalStore()
        await store.set_policy("delete_issue", ApprovalPolicy.ASK_ONCE)
        hil_store = InMemoryHILStore()
        hook = ApprovalHook(store, hil_store=hil_store)

        async def _approve_once() -> None:
            for _ in range(100):
                pending = await hil_store.list_pending()
                if pending:
                    await hil_store.respond(HILResponse(request_id=pending[0].request_id, approved=True))
                    return
                await asyncio.sleep(0)
            raise AssertionError("HIL request was never submitted")

        first, _ = await asyncio.gather(
            hook.check(_call(), session_id="sess-1"),
            _approve_once(),
        )
        assert first.approved is True

        # Second call in the same session must reuse the cached decision --
        # no second HIL request, and no hil_store required for it to work.
        hook_no_store = ApprovalHook(store, hil_store=None)
        second = await hook_no_store.check(_call(), session_id="sess-1")
        assert second.approved is True


class TestRiskLevelDefaultPolicies:
    async def test_critical_risk_auto_denies_by_default(self) -> None:
        store = InMemoryApprovalStore()

        class _FakeTool:
            risk_level = RiskLevel.CRITICAL

        class _FakeRegistry:
            def resolve_by_name(self, name: str) -> _FakeTool:
                return _FakeTool()

        hook = ApprovalHook(store, tool_registry=_FakeRegistry())

        decision = await hook.check(_call(name="drop_database"), session_id="sess-1")

        assert decision.approved is False
        assert decision.policy == ApprovalPolicy.AUTO_DENY
