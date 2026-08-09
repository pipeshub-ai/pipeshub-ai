"""`SideEffectFlag` + `track_side_effects`: Part C2/Part G of the task
engine plan -- the `POST_TOOL_USE` hook `runtime/executor.py`'s own
docstring names as `had_write_side_effect`'s producer, deferred out of
Phase 4 until this phase.

A scheduled task run is headless (`AgentRuntime(hooks=...)` built fresh per
`TaskSpecAssembler.assemble()` call, per-run, never shared) -- there is no
existing chat-side hook wiring to piggyback on, so this closes over a small
mutable flag object the caller (`TaskExecutor`) already holds, the same
"closure-captured object" idiom `app/agents/agent_loop/hooks/result_
accumulation.py`/`citations.py` use for surfacing hook-observed state back
to code outside `agent.run()`. `RunScope`/`StateSlot` was considered and
rejected: this flag only needs to survive until the CURRENT `agent.run()`/
`agent.resume()` call returns (the executor reads it immediately after),
never across a checkpoint/resume boundary itself -- persisting it would be
solving a problem this flag doesn't have.

`by_tag("category", "write")` is the same ad-hoc-but-repeated convention
already on every write-side connector tool and `SkillManageTool`/
`spawn_agent`'s write-tagged calls (no dedicated `Tag` constant exists to
import instead -- see `hooks/middleware/routing.py::by_tag`'s own
docstring for the canonical registration example this mirrors).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.hooks.middleware.routing import by_tag

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.agent_loop_lib.hooks.middleware.context import ToolCallContext, ToolResultContext
    from app.agent_loop_lib.hooks.middleware.pipeline import Next
    from app.agent_loop_lib.hooks.registry import HookRegistry

__all__ = ["SideEffectFlag", "block_writes_for_dry_run", "track_side_effects"]

logger = logging.getLogger(__name__)


@dataclass
class SideEffectFlag:
    """Sticky -- once True, stays True for the rest of the run. A single
    write tool call having fired at all (regardless of whether the OVERALL
    run later succeeds) is what makes an uncheckpointed crash-restart
    unsafe (`TaskExecutor._recover_or_finalize`'s side-effect gate), not
    whether that particular call itself succeeded."""

    had_write_side_effect: bool = False


def track_side_effects(
    hooks: "HookRegistry",
    flag: SideEffectFlag,
    *,
    on_first_side_effect: "Callable[[], Awaitable[None]] | None" = None,
) -> None:
    """Registers a `POST_TOOL_USE` middleware, scoped to `category=write`
    tools via `by_tag`, that sets `flag.had_write_side_effect = True` the
    first (and every subsequent) time one fires this run. Fires regardless
    of `ToolResultContext.tool_response.success` -- a write call that
    executed but reported failure (e.g. a Slack API 500 after the message
    was already partially processed) is exactly the ambiguous case the
    side-effect gate exists to protect against; only a call rejected
    before execution (a `PRE_TOOL_USE` deny, which never reaches
    `POST_TOOL_USE` at all) is truly a no-op.

    `on_first_side_effect`, when given, is awaited exactly once, the FIRST
    time the flag flips -- `runtime/executor.py` uses this to eagerly
    persist `TaskRun.had_write_side_effect` to the run store immediately,
    rather than only at `_finalize_result` time, since the crash this flag
    exists to protect against is exactly a crash that never reaches
    `_finalize_result`. A failure in the callback is logged and swallowed
    (never denies/blocks the tool call itself, and never re-raises into
    the pipeline) -- a best-effort durability write losing a race with a
    crash is the scenario this flag already exists to handle conservatively;
    it must not itself become a new way for a write tool call to fail.
    """

    async def _post_tool_use(ctx: "ToolResultContext", next_fn: "Next") -> None:
        is_first = not flag.had_write_side_effect
        flag.had_write_side_effect = True
        if is_first and on_first_side_effect is not None:
            try:
                await on_first_side_effect()
            except Exception:
                logger.exception("on_first_side_effect callback failed for tool %s", ctx.tool_path)
        await next_fn()

    hooks.on(HookEvent.POST_TOOL_USE).use(by_tag("category", "write"), _post_tool_use)


def block_writes_for_dry_run(hooks: "HookRegistry") -> None:
    """Registers a `PRE_TOOL_USE` deny on `category=write` tools so a dry run
    of an `agent_task` workflow cannot mutate anything external.

    The code-workflow path enforces this in the SDK/broker; agent-task runs
    have no such choke point, so without this a "dry run" of the common case
    posts real Slack messages and files real Jira tickets. A PRE_TOOL_USE
    denial short-circuits before execution, so it never reaches
    `POST_TOOL_USE` and never flips `SideEffectFlag`.
    """

    async def _pre_tool_use(ctx: "ToolCallContext", next_fn: "Next") -> None:
        ctx.deny(
            f"Dry run: '{ctx.tool_path}' is a write tool and was not executed. "
            "Run the workflow for real to perform this action."
        )
        await next_fn()

    hooks.on(HookEvent.PRE_TOOL_USE).use(by_tag("category", "write"), _pre_tool_use)
