from __future__ import annotations

from app.agent_loop_lib.core.types import ToolCall
from app.agent_loop_lib.modules.stores.approval.base import (
    ApprovalPolicy,
    RiskLevel,
)
from app.agent_loop_lib.modules.stores.approval.hook import ApprovalHook
from app.agent_loop_lib.modules.stores.approval.in_memory import InMemoryApprovalStore


async def test_policy_requiring_human_denies_when_hil_store_is_unavailable() -> None:
    store = InMemoryApprovalStore()
    await store.set_policy("write_external", ApprovalPolicy.ASK_EACH_TIME)
    hook = ApprovalHook(store=store, hil_store=None)

    decision = await hook.check(
        ToolCall(id="call-1", name="write_external", arguments={"value": "x"}),
        session_id="session-1",
    )

    assert decision.approved is False
    assert decision.policy is ApprovalPolicy.ASK_EACH_TIME
    assert decision.reason == "human approval is unavailable"


async def test_auto_approve_policy_does_not_require_hil_store() -> None:
    store = InMemoryApprovalStore()
    await store.set_policy("read_data", ApprovalPolicy.AUTO_APPROVE)
    hook = ApprovalHook(store=store, hil_store=None)

    decision = await hook.check(
        ToolCall(id="call-2", name="read_data", arguments={}),
        session_id="session-1",
    )

    assert decision.approved is True
    assert decision.risk_level is RiskLevel.LOW
