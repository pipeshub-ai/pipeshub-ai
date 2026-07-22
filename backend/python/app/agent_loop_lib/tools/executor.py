"""`ToolExecutor`: owns the full resolve -> PreToolUse -> execute -> PostToolUse sequence.

Replaces the old bare "resolve + call `Tool.execute()`" executor: every call
now flows through the `HookRegistry`'s PRE_TOOL_USE/POST_TOOL_USE pipelines
(permission, mode, approval, budget, audit-log middleware, ...), exactly once,
regardless of caller. The Agent's turn loop (`agent/tool_loop.py`), the
`execute_code` sandbox RPC bridge (`ControlPlane._execute_code_dispatch`), and
any future entry point all call `call_tool()` so no caller can accidentally
bypass a hook by calling `Tool.execute()` directly.

Bridges two different result types at this one boundary:
    - `agent_loop.tools.base.ToolOutput` (`success`/`data`/`error`/`sources`)
      is what `Tool.execute()` returns — the tool-internal shape.
    - `agent_loop.core.types.ToolResult` (`tool_call_id`/`name`/`content`/
      `is_error`/`sources`) is what the Agent loop, transport layer, and
      message pipeline expect.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from app.agent_loop_lib.core.types import ToolCall
from app.agent_loop_lib.core.types import ToolResult as CoreToolResult
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.hooks.middleware.context import (
    ToolCallContext,
    ToolResultContext,
)
from app.agent_loop_lib.hooks.middleware.decisions import PostDecision, PreDecision
from app.agent_loop_lib.hooks.registry import HookRegistry
from app.agent_loop_lib.tools.base import ToolOutput
from app.agent_loop_lib.tools.errors import ToolNotFoundError, ToolValidationError
from app.agent_loop_lib.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from app.agent_loop_lib.core.scope import ToolScope
    from app.agent_loop_lib.tools.base import Tool

__all__ = ["ToolExecutor"]

OverrideExecute = Callable[[], Awaitable[CoreToolResult]]

# Invoked exactly once, only when PRE_TOOL_USE escalates to `PreDecision.ASK`
# (see `call_tool()` below). Takes the call plus the escalating middleware's
# reason, returns whether a human approved it. Left `None` by a caller that
# hasn't wired a HIL store — ASK then degrades to the same behavior as DENY,
# since there is no one to ask.
OnAsk = Callable[[ToolCall, str], Awaitable[bool]]

_USAGE_HINT_DESCRIPTION_LIMIT = 100


def _usage_hint(tool: "Tool") -> str:
    """Compact, one-line-per-parameter usage summary built from `tool.
    parameters` — appended to a `ToolValidationError` so the model sees
    exactly what a correct call looks like (name, type, required/optional,
    default, enum, truncated description) instead of only the one field
    that failed."""
    lines = [f"Correct usage of `{tool.name}`:"]
    for param in tool.parameters:
        bits = [param.type.value]
        bits.append("required" if param.required else f"optional, default={param.default!r}")
        if param.enum:
            bits.append(f"one of {param.enum}")
        description = param.description.strip()
        if len(description) > _USAGE_HINT_DESCRIPTION_LIMIT:
            description = description[: _USAGE_HINT_DESCRIPTION_LIMIT - 3] + "..."
        lines.append(f"  - {param.name} ({', '.join(bits)}): {description}")
    return "\n".join(lines)


class ToolExecutor:
    """Resolves a tool by name and dispatches it through the hook pipelines."""

    def __init__(
        self,
        registry: ToolRegistry,
        kernel: HookRegistry | None = None,
        *,
        opik_enabled: bool = False,
        opik_project_name: str | None = None,
    ) -> None:
        self._registry = registry
        self._kernel = kernel or HookRegistry()
        # See `transport/opik_tracing.py::maybe_start_tool_span` — set from
        # `AgentRuntime.opik_enabled` by `Agent.__init__` so every tool call
        # (including special routes dispatched via `override_execute`) gets
        # an Opik "tool" span nested under the active agent/LLM span.
        self._opik_enabled = opik_enabled
        self._opik_project_name = opik_project_name

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    @property
    def kernel(self) -> HookRegistry:
        return self._kernel

    async def call_tool(
        self,
        call: ToolCall,
        *,
        session_id: str | None = None,
        caller: str = "agent",
        override_execute: OverrideExecute | None = None,
        on_denied: Callable[[str], Awaitable[None]] | None = None,
        on_ask: OnAsk | None = None,
        scope: "ToolScope | None" = None,
    ) -> CoreToolResult:
        """Run one tool call through PreToolUse -> execute -> PostToolUse.

        `override_execute`, when given, replaces the normal `Tool.execute()`
        call with a caller-supplied coroutine that already produces a
        `core.types.ToolResult` — used for tool-loop special routes
        (spawn_agent, clarify, best_of_n, replan, handoff) whose real
        behavior needs Agent-level state a stateless `Tool` instance can't
        hold. The PreToolUse/PostToolUse pipelines still run around it
        exactly as they would for a normal tool, so permission/mode/approval/
        audit-log middleware apply uniformly regardless of route.

        `on_denied`, when given, is awaited with the denial reason if
        PRE_TOOL_USE ultimately denies (either a direct DENY, or an ASK that
        came back unapproved) — lets callers (e.g. the agent turn loop)
        record a distinct "blocked" event/timeline entry instead of treating
        it like an ordinary execution failure.

        `on_ask`, when given, is awaited with `(call, reason)` if PRE_TOOL_USE
        escalates to `PreDecision.ASK` — the human-in-the-loop checkpoint/
        suspend seam (see `agent/observability.py::handle_tool_approval`,
        which pauses on a `HILStore` the same way the `clarify` tool does).
        Returns whether the call is approved; the tool actually executes only
        if so. Without `on_ask` wired, ASK has no one to ask and degrades to
        the same outcome as DENY — this executor is caller-agnostic and holds
        no `HILStore`/checkpoint state of its own.

        `scope`, when given (always set by the real turn loop; `None` in
        standalone/test calls), is attached to both decision contexts so
        middleware can reach `ctx.scope.turn.run` for ambient run state —
        see `core/scope.py`.
        """
        path = self._registry.path_for_name(call.name)
        tags = self._registry.tags_for_name(call.name) if path is not None else ()
        resolved_path = path or f"/unresolved/{call.name}"

        if scope is not None:
            # Mirror the resolution onto the SAME scope object special-route
            # handlers were already handed (see `agent/tool_loop.py`) — it's
            # constructed with a placeholder `tool_path=""` before this
            # resolution happens, since resolving is this executor's job.
            scope.tool_path = resolved_path
            scope.tags = tags

        pre_ctx = ToolCallContext(
            tool_path=resolved_path,
            tool_input=dict(call.arguments),
            caller=caller,
            session_id=session_id,
            tags=tags,
            scope=scope,
        )
        await self._kernel.on(HookEvent.PRE_TOOL_USE).dispatch(pre_ctx)

        approved = pre_ctx.decision == PreDecision.ALLOW
        if pre_ctx.decision == PreDecision.ASK:
            approved = await on_ask(call, pre_ctx.decision_reason or "") if on_ask is not None else False

        if not approved:
            reason = pre_ctx.decision_reason or f"tool call to {call.name!r} was not approved"
            if on_denied is not None:
                await on_denied(reason)
            return CoreToolResult(tool_call_id=call.id, name=call.name, content=reason, is_error=True)

        from app.agent_loop_lib.transport.opik_tracing import (
            maybe_start_tool_span,
            record_tool_span_output,
        )

        with maybe_start_tool_span(
            enabled=self._opik_enabled,
            name=call.name,
            arguments=dict(call.arguments),
            project_name=self._opik_project_name,
        ) as span:
            tool_result = await self._run(call, pre_ctx.tool_input, override_execute)
            record_tool_span_output(span, result=tool_result)

        post_ctx = ToolResultContext(
            tool_path=pre_ctx.tool_path,
            tool_use_id=pre_ctx.tool_use_id,
            tool_response=tool_result,
            caller=caller,
            session_id=session_id,
            tags=tags,
            scope=scope,
            metadata=dict(pre_ctx.metadata),
        )
        await self._kernel.on(HookEvent.POST_TOOL_USE).dispatch(post_ctx)

        final = post_ctx.tool_response
        if post_ctx.decision == PostDecision.BLOCK:
            reason = post_ctx.decision_reason or "tool result was blocked"
            return CoreToolResult(tool_call_id=call.id, name=call.name, content=reason, is_error=True)

        return CoreToolResult(
            tool_call_id=call.id,
            name=call.name,
            content=final.data if final.success else (final.error or "tool execution failed"),
            is_error=not final.success,
            sources=list(final.sources),
        )

    async def _run(
        self,
        call: ToolCall,
        tool_input: dict,
        override_execute: OverrideExecute | None,
    ) -> ToolOutput:
        try:
            if override_execute is not None:
                core_result = await override_execute()
                return ToolOutput(
                    success=not core_result.is_error,
                    data=core_result.content,
                    error=str(core_result.content) if core_result.is_error else None,
                    sources=list(core_result.sources),
                )
            tool = self._registry.resolve_by_name(call.name)
        except ToolNotFoundError:
            return ToolOutput(success=False, error=f"Unknown tool: {call.name}")

        try:
            normalized = dict(tool_input)
            tool.validate(normalized)
        except ToolValidationError as exc:
            # A bare exception string ("missing required argument 'code'")
            # gives a weak model nothing to correct against beyond guessing
            # again — appending the tool's own parameter list lets it fix
            # the call in one turn instead of flailing until
            # `ToolErrorTracker`'s 3-strike limit blocks the tool outright.
            return ToolOutput(success=False, error=f"{exc}\n\n{_usage_hint(tool)}")
        except Exception as exc:
            return ToolOutput(success=False, error=str(exc))

        try:
            return await tool.execute(**normalized)
        except Exception as exc:
            return ToolOutput(success=False, error=str(exc))

    async def execute(self, call: ToolCall) -> CoreToolResult:
        """Backward-compatible alias for `call_tool` with no session/override."""
        return await self.call_tool(call)
