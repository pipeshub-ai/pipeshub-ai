"""The final PRE_MODEL guard (`context_invariants.py`) and how its errors leave
the pipeline."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.agent.hook_dispatch import dispatch_pre_model
from app.agent_loop_lib.context.base import ContextBudget
from app.agent_loop_lib.context.errors import ContextBudgetExceeded
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    TextPart,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.hooks.middleware.builtin.context_invariants import (
    shape_context_invariants,
)
from app.agent_loop_lib.hooks.registry import HookRegistry

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import ModelCallContext
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware

_QUESTION_B = "QUESTION B: which practice do I log into?"


def _chat() -> list:
    return [
        UserMessage(content="QUESTION A: how do reminders work?"),
        AssistantMessage(content=[TextPart(text="Reminders go out by SMS.")]),
        UserMessage(content=_QUESTION_B),
        AssistantMessage(tool_calls=[ToolCall(id="c1", name="search", arguments={})]),
        ToolMessage(content="practice picker docs", tool_call_id="c1"),
        AssistantMessage(tool_calls=[ToolCall(id="c2", name="fetch", arguments={})]),
        ToolMessage(content="default practice docs", tool_call_id="c2"),
    ]


def _budget(max_tokens: int = 50_000, reserve: int = 8_000) -> ContextBudget:
    return ContextBudget(max_tokens=max_tokens, model="test", reserved_output_tokens=reserve)


def _drop_current_request() -> "Middleware[ModelCallContext]":
    """A broken shaper: loses the current question (the bug this guards)."""

    async def _middleware(ctx: ModelCallContext, next_fn) -> None:
        ctx.messages = [m for m in ctx.messages if getattr(m, "content", None) != _QUESTION_B]
        await next_fn()

    return _middleware


def _humans(messages: list) -> list[str]:
    return [m.content for m in messages if isinstance(m, UserMessage) and not m.injected]


async def _shape(*middlewares, messages=None, budget=None) -> list:
    kernel = HookRegistry()
    for mw in middlewares:
        kernel.on(HookEvent.PRE_MODEL).use(mw)
    return await dispatch_pre_model(kernel, messages or _chat(), budget or _budget(), turn_index=7)


class TestCurrentRequestRepair:
    @pytest.mark.asyncio
    async def test_untouched_request_passes_through(self, caplog) -> None:
        with caplog.at_level(logging.ERROR):
            sent = await _shape(shape_context_invariants())
        assert sent == _chat()
        assert "context_invariant_repaired" not in caplog.text

    @pytest.mark.asyncio
    async def test_lost_request_is_restored_before_the_runs_tool_calls(self, caplog) -> None:
        with caplog.at_level(logging.ERROR):
            sent = await _shape(_drop_current_request(), shape_context_invariants())

        assert _humans(sent)[-1] == _QUESTION_B
        restored_at = next(i for i, m in enumerate(sent) if getattr(m, "content", None) == _QUESTION_B)
        first_call = next(i for i, m in enumerate(sent) if isinstance(m, AssistantMessage) and m.tool_calls)
        assert restored_at == first_call - 1
        assert "context_invariant_repaired" in caplog.text
        assert "turn=7" in caplog.text
        assert _QUESTION_B not in caplog.text, "logs carry ids and counts, never content"

    @pytest.mark.asyncio
    async def test_restored_after_summaries_when_the_runs_calls_were_summarised(self) -> None:
        summary = UserMessage(content="[Loop compaction: 4 messages compressed]", injected=True)

        def _summarise_everything() -> "Middleware[ModelCallContext]":
            async def _middleware(ctx: ModelCallContext, next_fn) -> None:
                ctx.messages = [summary]
                await next_fn()

            return _middleware

        sent = await _shape(_summarise_everything(), shape_context_invariants())
        assert sent == [summary, UserMessage(content=_QUESTION_B)]

    @pytest.mark.asyncio
    async def test_misplaced_request_moves_back_to_last_human_position(self) -> None:
        def _move_request_first() -> "Middleware[ModelCallContext]":
            async def _middleware(ctx: ModelCallContext, next_fn) -> None:
                rest = [m for m in ctx.messages if getattr(m, "content", None) != _QUESTION_B]
                stray = UserMessage(content="a stray human-looking line")
                ctx.messages = [UserMessage(content=_QUESTION_B), *rest, stray]
                await next_fn()

            return _middleware

        sent = await _shape(_move_request_first(), shape_context_invariants())
        assert sum(1 for m in sent if getattr(m, "content", None) == _QUESTION_B) == 1
        assert _humans(sent)[-1] == _QUESTION_B


class TestPairingRepair:
    @pytest.mark.asyncio
    async def test_orphaned_tool_result_is_dropped(self, caplog) -> None:
        messages = [*_chat(), ToolMessage(content="orphan", tool_call_id="nobody")]
        with caplog.at_level(logging.WARNING):
            sent = await _shape(shape_context_invariants(), messages=messages)
        assert all(getattr(m, "tool_call_id", None) != "nobody" for m in sent)
        assert "broke pairing" in caplog.text


class TestWindowLimit:
    @pytest.mark.asyncio
    async def test_past_the_window_the_error_reaches_the_caller(self, caplog) -> None:
        huge = [UserMessage(content="question " + "x" * 40_000)]
        with caplog.at_level(logging.ERROR), pytest.raises(ContextBudgetExceeded):
            await _shape(shape_context_invariants(), messages=huge, budget=_budget(max_tokens=1_000, reserve=0))
        assert "context_budget_exceeded" in caplog.text

    @pytest.mark.asyncio
    async def test_over_budget_but_inside_the_window_is_sent(self) -> None:
        messages = [UserMessage(content="question " + "x" * 8_000)]
        sent = await _shape(shape_context_invariants(), messages=messages, budget=_budget(max_tokens=1_000))
        assert sent == messages

    @pytest.mark.asyncio
    async def test_other_shaper_errors_are_still_recorded_and_skipped(self) -> None:
        def _broken() -> "Middleware[ModelCallContext]":
            async def _middleware(ctx: ModelCallContext, next_fn) -> None:
                raise RuntimeError("a formatter bug")

            return _middleware

        sent = await _shape(_broken(), shape_context_invariants())
        assert sent == _chat()
