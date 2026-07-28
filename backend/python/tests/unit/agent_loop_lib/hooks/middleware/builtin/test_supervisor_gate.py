"""Tests for the supervisor_confidence_gate POST_TOOL_USE middleware.

Regression coverage for the fix-confidence-gate todo: `create_plan`'s result
comes back in one of two shapes (a dict from the structured `steps` path, or
free-form markdown text with a trailing `Confidence:` line from the
`DefaultPlanner` path) and the gate must evaluate confidence correctly for
both, rather than being a permanent no-op.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.agent_loop_lib.hooks.middleware.builtin.supervisor_gate import (
    confidence_from_tool_response,
    supervisor_confidence_gate,
)
from app.agent_loop_lib.hooks.middleware.context import PostDecision, ToolResultContext
from app.agent_loop_lib.tools.base import Tag, ToolOutput
from app.agent_loop_lib.tools.tags import TAG_PLANNING_CREATE_PLAN


def _ctx(
    *, tool_path: str, data: object, success: bool = True, tags: tuple[Tag, ...] = ()
) -> ToolResultContext:
    return ToolResultContext(
        tool_path=tool_path,
        tool_use_id=uuid4(),
        tool_response=ToolOutput(success=success, data=data),
        tags=tags,
    )


def _create_plan_ctx(*, data: object, success: bool = True) -> ToolResultContext:
    """The gate dispatches on `TAG_PLANNING_CREATE_PLAN`, not the literal
    tool path — real dispatch gets this tag from `CreatePlanTool.tags` via
    `ToolRegistry.tags_for_name`, mirrored here explicitly."""
    return _ctx(
        tool_path="/toolsets/builtin/create_plan", data=data, success=success,
        tags=(TAG_PLANNING_CREATE_PLAN,),
    )


async def _noop_next() -> None:
    pass


class TestConfidenceFromToolResponse:
    def test_dict_payload_from_structured_steps_path(self) -> None:
        from app.agent_loop_lib.core.types import Confidence

        assert confidence_from_tool_response({"plan": "...", "confidence": "low"}) == Confidence.LOW
        assert confidence_from_tool_response({"plan": "...", "confidence": "high"}) == Confidence.HIGH

    def test_markdown_trailing_line_from_freeform_path(self) -> None:
        from app.agent_loop_lib.core.types import Confidence

        text = "1. Phase One: do the thing\n2. Phase Two: do another thing\nConfidence: low"
        assert confidence_from_tool_response(text) == Confidence.LOW

    def test_dict_without_confidence_key_is_unrecognizable(self) -> None:
        assert confidence_from_tool_response({"plan": "..."}) is None

    def test_non_dict_non_str_is_unrecognizable(self) -> None:
        assert confidence_from_tool_response(None) is None
        assert confidence_from_tool_response(42) is None


class TestSupervisorConfidenceGate:
    @pytest.mark.asyncio
    async def test_low_confidence_dict_payload_blocks(self) -> None:
        ctx = _create_plan_ctx(data={"plan": "the plan", "confidence": "low"})
        await supervisor_confidence_gate()(ctx, _noop_next)
        assert ctx.decision == PostDecision.BLOCK

    @pytest.mark.asyncio
    async def test_low_confidence_markdown_line_blocks(self) -> None:
        ctx = _create_plan_ctx(data="1. Do the thing\nConfidence: low")
        await supervisor_confidence_gate()(ctx, _noop_next)
        assert ctx.decision == PostDecision.BLOCK

    @pytest.mark.asyncio
    async def test_medium_confidence_passes(self) -> None:
        ctx = _create_plan_ctx(data={"plan": "the plan", "confidence": "medium"})
        await supervisor_confidence_gate()(ctx, _noop_next)
        assert ctx.decision == PostDecision.CONTINUE

    @pytest.mark.asyncio
    async def test_high_confidence_passes(self) -> None:
        ctx = _create_plan_ctx(data="1. Do the thing\nConfidence: high")
        await supervisor_confidence_gate()(ctx, _noop_next)
        assert ctx.decision == PostDecision.CONTINUE

    @pytest.mark.asyncio
    async def test_missing_confidence_line_defaults_to_medium_and_passes(self) -> None:
        ctx = _create_plan_ctx(data="1. Do the thing\n2. Do another thing")
        await supervisor_confidence_gate()(ctx, _noop_next)
        assert ctx.decision == PostDecision.CONTINUE

    @pytest.mark.asyncio
    async def test_non_create_plan_tool_is_a_noop(self) -> None:
        ctx = _ctx(tool_path="/toolsets/builtin/jira_search", data={"confidence": "low"})
        await supervisor_confidence_gate()(ctx, _noop_next)
        assert ctx.decision == PostDecision.CONTINUE

    @pytest.mark.asyncio
    async def test_failed_create_plan_is_a_noop(self) -> None:
        ctx = _create_plan_ctx(data=None, success=False)
        await supervisor_confidence_gate()(ctx, _noop_next)
        assert ctx.decision == PostDecision.CONTINUE

    @pytest.mark.asyncio
    async def test_next_is_always_called(self) -> None:
        calls = []

        async def _tracking_next() -> None:
            calls.append(1)

        ctx = _create_plan_ctx(data={"plan": "the plan", "confidence": "low"})
        await supervisor_confidence_gate()(ctx, _tracking_next)
        assert calls == [1]
