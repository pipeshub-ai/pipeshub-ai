"""Tests for `tools/approval.py` — the `ApprovalHandler` port and its
`StorageBackedApprovalHandler`/`require_approval` bridge to the existing
`ApprovalHook` flow."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

from app.agent_loop_lib.hooks.middleware.context import ToolCallContext
from app.agent_loop_lib.hooks.middleware.decisions import PreDecision
from app.agent_loop_lib.modules.stores.approval.base import (
    ApprovalDecision,
    ApprovalPolicy,
    RiskLevel,
)
from app.agent_loop_lib.tools.approval import (
    ApprovalHandler,
    StorageBackedApprovalHandler,
    require_approval,
)


def _ctx(*, tool_path: str = "/toolsets/jira/create_issue", tool_input: dict | None = None) -> ToolCallContext:
    return ToolCallContext(
        tool_path=tool_path,
        tool_input=tool_input if tool_input is not None else {"summary": "do a thing"},
        tool_use_id=uuid4(),
        session_id="sess-1",
    )


def _hook(decision: ApprovalDecision) -> AsyncMock:
    hook = AsyncMock()
    hook.check = AsyncMock(return_value=decision)
    return hook


class TestStorageBackedApprovalHandler:
    async def test_approved_call_returns_true_and_leaves_metadata_untouched(self) -> None:
        decision = ApprovalDecision(
            tool_name="create_issue", risk_level=RiskLevel.LOW,
            policy=ApprovalPolicy.AUTO_APPROVE, approved=True,
        )
        handler = StorageBackedApprovalHandler(_hook(decision))
        ctx = _ctx()

        approved = await handler.request_approval(ctx)

        assert approved is True
        assert "approval_decision_id" not in ctx.metadata

    async def test_denied_call_returns_false_and_stamps_decision_id(self) -> None:
        decision = ApprovalDecision(
            decision_id="dec-42", tool_name="create_issue", risk_level=RiskLevel.HIGH,
            policy=ApprovalPolicy.ASK_EACH_TIME, approved=False,
        )
        handler = StorageBackedApprovalHandler(_hook(decision))
        ctx = _ctx()

        approved = await handler.request_approval(ctx)

        assert approved is False
        assert ctx.metadata["approval_decision_id"] == "dec-42"

    async def test_tool_name_is_the_last_path_segment(self) -> None:
        decision = ApprovalDecision(
            tool_name="create_issue", risk_level=RiskLevel.LOW,
            policy=ApprovalPolicy.AUTO_APPROVE, approved=True,
        )
        hook = _hook(decision)
        handler = StorageBackedApprovalHandler(hook)
        ctx = _ctx(tool_path="/toolsets/jira/create_issue", tool_input={"a": 1})

        await handler.request_approval(ctx)

        call = hook.check.await_args.args[0]
        assert call.name == "create_issue"
        assert call.id == str(ctx.tool_use_id)
        assert call.arguments == {"a": 1}
        assert hook.check.await_args.kwargs == {"session_id": "sess-1"}

    async def test_conforms_to_approval_handler_protocol(self) -> None:
        decision = ApprovalDecision(
            tool_name="x", risk_level=RiskLevel.LOW, policy=ApprovalPolicy.AUTO_APPROVE, approved=True,
        )
        handler = StorageBackedApprovalHandler(_hook(decision))
        assert isinstance(handler, ApprovalHandler)


class TestRequireApproval:
    async def test_approved_calls_next_and_leaves_decision_allow(self) -> None:
        handler = AsyncMock()
        handler.request_approval = AsyncMock(return_value=True)
        next_fn = AsyncMock()
        ctx = _ctx()

        middleware = require_approval(handler)
        await middleware(ctx, next_fn)

        next_fn.assert_awaited_once()
        assert ctx.decision == PreDecision.ALLOW

    async def test_denied_does_not_call_next_and_denies_with_tool_path(self) -> None:
        handler = AsyncMock()
        handler.request_approval = AsyncMock(return_value=False)
        next_fn = AsyncMock()
        ctx = _ctx(tool_path="/toolsets/jira/delete_issue")

        middleware = require_approval(handler)
        await middleware(ctx, next_fn)

        next_fn.assert_not_awaited()
        assert ctx.decision == PreDecision.DENY
        assert ctx.decision_reason == "tool call to '/toolsets/jira/delete_issue' was not approved"

    async def test_handler_is_invoked_with_the_context(self) -> None:
        handler = AsyncMock()
        handler.request_approval = AsyncMock(return_value=True)
        ctx = _ctx()

        await require_approval(handler)(ctx, AsyncMock())

        handler.request_approval.assert_awaited_once_with(ctx)
