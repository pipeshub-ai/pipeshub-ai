"""Unit tests for `SideEffectFlag`/`track_side_effects` -- the
`POST_TOOL_USE` hook `TaskExecutor` wires per run so a crash mid-run can
tell (via `TaskRun.had_write_side_effect`) whether an uncheckpointed
restart would be unsafe (Phase 4's side-effect gate, populated by this
Phase 7 hook)."""
from __future__ import annotations

import uuid

from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.hooks.middleware.context import ToolResultContext
from app.agent_loop_lib.hooks.registry import HookRegistry
from app.agent_loop_lib.tools.base import Tag, ToolOutput
from app.agents.agent_loop.hooks.task_side_effect import (
    SideEffectFlag,
    track_side_effects,
)


def _write_ctx(tool_path: str = "/toolsets/slack/send_message") -> ToolResultContext:
    return ToolResultContext(
        tool_path=tool_path, tool_use_id=uuid.uuid4(),
        tool_response=ToolOutput(success=True),
        tags=(Tag("category", "write"),),
    )


def _read_ctx(tool_path: str = "/toolsets/slack/list_channels") -> ToolResultContext:
    return ToolResultContext(
        tool_path=tool_path, tool_use_id=uuid.uuid4(),
        tool_response=ToolOutput(success=True),
        tags=(Tag("category", "read"),),
    )


class TestTrackSideEffects:
    async def test_write_tool_call_sets_the_flag(self) -> None:
        hooks = HookRegistry()
        flag = SideEffectFlag()
        track_side_effects(hooks, flag)

        await hooks.on(HookEvent.POST_TOOL_USE).dispatch(_write_ctx())

        assert flag.had_write_side_effect is True

    async def test_read_tool_call_never_sets_the_flag(self) -> None:
        hooks = HookRegistry()
        flag = SideEffectFlag()
        track_side_effects(hooks, flag)

        await hooks.on(HookEvent.POST_TOOL_USE).dispatch(_read_ctx())

        assert flag.had_write_side_effect is False

    async def test_flag_stays_sticky_after_a_later_read_call(self) -> None:
        hooks = HookRegistry()
        flag = SideEffectFlag()
        track_side_effects(hooks, flag)

        await hooks.on(HookEvent.POST_TOOL_USE).dispatch(_write_ctx())
        await hooks.on(HookEvent.POST_TOOL_USE).dispatch(_read_ctx())

        assert flag.had_write_side_effect is True

    async def test_flag_flips_even_when_the_tool_call_reported_failure(self) -> None:
        """A write call that executed but reported failure (e.g. a Slack
        API 500 after the message was already sent) is exactly the
        ambiguous case the side-effect gate protects against."""
        hooks = HookRegistry()
        flag = SideEffectFlag()
        track_side_effects(hooks, flag)
        ctx = ToolResultContext(
            tool_path="/toolsets/slack/send_message", tool_use_id=uuid.uuid4(),
            tool_response=ToolOutput(success=False, error="boom"),
            tags=(Tag("category", "write"),),
        )

        await hooks.on(HookEvent.POST_TOOL_USE).dispatch(ctx)

        assert flag.had_write_side_effect is True

    async def test_on_first_side_effect_callback_fires_exactly_once(self) -> None:
        hooks = HookRegistry()
        flag = SideEffectFlag()
        calls: list[int] = []

        async def _on_first() -> None:
            calls.append(1)

        track_side_effects(hooks, flag, on_first_side_effect=_on_first)

        await hooks.on(HookEvent.POST_TOOL_USE).dispatch(_write_ctx())
        await hooks.on(HookEvent.POST_TOOL_USE).dispatch(_write_ctx())

        assert len(calls) == 1

    async def test_on_first_side_effect_never_invoked_for_read_only_calls(self) -> None:
        hooks = HookRegistry()
        flag = SideEffectFlag()
        calls: list[int] = []

        async def _on_first() -> None:
            calls.append(1)

        track_side_effects(hooks, flag, on_first_side_effect=_on_first)

        await hooks.on(HookEvent.POST_TOOL_USE).dispatch(_read_ctx())

        assert calls == []

    async def test_on_first_side_effect_failure_is_swallowed_and_never_blocks(self) -> None:
        """A best-effort durability write losing a race with a crash must
        never itself become a new way for the tool call to fail."""
        hooks = HookRegistry()
        flag = SideEffectFlag()

        async def _failing_callback() -> None:
            raise RuntimeError("run store unreachable")

        track_side_effects(hooks, flag, on_first_side_effect=_failing_callback)

        ctx = await hooks.on(HookEvent.POST_TOOL_USE).dispatch(_write_ctx())

        assert flag.had_write_side_effect is True
        assert ctx.tool_response.success is True
