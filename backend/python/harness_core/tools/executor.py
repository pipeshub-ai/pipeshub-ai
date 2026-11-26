# pyright: basic
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

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import TYPE_CHECKING

from harness_core.deadline import call_deadline, remaining_s
from harness_core.effects.ambiguity import (
    note_ambiguous_send,
    track_sends,
    unknown_effect,
)
from harness_core.effects.failures import known_code, retry_after_s, tool_error_code
from harness_core.effects.types import EffectUnknown
from harness_core.kernel.core.messages import malformed_call_error
from harness_core.kernel.core.tool_content import UNKNOWN_TOOL_ERROR_PREFIX
from harness_core.kernel.core.types import Source, ToolCall
from harness_core.kernel.core.types import ToolResult as CoreToolResult
from harness_core.kernel.hooks.events import HookEvent
from harness_core.kernel.hooks.middleware.context import (
    ToolCallContext,
    ToolResultContext,
)
from harness_core.kernel.hooks.middleware.decisions import (
    PendingApproval,
    PostDecision,
    PreDecision,
)
from harness_core.kernel.hooks.registry import HookRegistry
from harness_core.log_lines import (
    already_logged,
    classify_error,
    credential_value_mask,
    level_for,
    log_event,
    log_failure,
    log_fields,
    redact_secrets_in_text,
)
from harness_core.tools.base import ToolOutput
from harness_core.tools.errors import ToolNotFoundError, ToolValidationError
from harness_core.tools.rate_limit import RateLimitBudget
from harness_core.tools.registry import ToolRegistry
from harness_core.tools.time_bound import BoundExceeded, ToolTimeBounds, run_bounded

if TYPE_CHECKING:
    from harness_core.kernel.core.scope import ToolScope
    from harness_core.tools.base import Tool
    from harness_core.tools.invocation import InvocationJournal

__all__ = ["UNKNOWN_TOOL_ERROR_PREFIX", "ToolExecutor", "collapse_repeated_name", "log_tool_call_end"]

logger = logging.getLogger("harness_core.tools")

OverrideExecute = Callable[[], Awaitable[CoreToolResult]]


# Invoked exactly once, only when PRE_TOOL_USE escalates to `PreDecision.ASK`
# (see `call_tool()` below). Takes the call plus the escalating middleware's
# reason, returns whether a human approved it. Left `None` by a caller that
# hasn't wired a HIL store — ASK then degrades to the same behavior as DENY,
# since there is no one to ask.
OnAsk = Callable[[ToolCall, str], Awaitable[bool]]
OnPending = Callable[[ToolCall, PendingApproval], Awaitable[None]]

_USAGE_HINT_DESCRIPTION_LIMIT = 100
MAX_LOGGED_TOOL_NAME = 128
# The `ToolOutput.end_fields` keys `tool_call.end` carries besides `reason` (`level` is read apart: not a field).
_END_FIELD_KEYS = ("error_class", "status", "mcp_reason")
_UNKNOWN_TOOL = MappingProxyType({"reason": "unknown_tool", "error_class": "not_found"})
_EXCEPTION = MappingProxyType({"reason": "exception"})


def log_tool_call_end(
    *, outcome: str, reason: str | None = None, error_class: str | None = None,
    cause: BaseException | None = None, tool: str | None = None, level: int | None = None, **fields: object,
) -> None:
    """The one ``tool_call.end`` line of a tool call: ``outcome`` is success, failure or skipped,
    ``reason`` the exit (exception, tool_error, bad_args, denied, ...). A failure with a ``cause``
    is logged once with its class and no error text (it can echo the arguments); another failure
    at its class's level, WARNING when unclassified; success and skipped at INFO. A call parked
    for approval isn't over yet: DEBUG, and the settled card logs the line. Pass ``tool`` and
    ``tool_call_id`` where `call_tool` hasn't bound them. ``level`` (the ``level`` key of
    ``ToolOutput.end_fields``) lowers a ``misconfigured`` failure's line, a repeat inside its hourly
    gate; for any other class, or a higher level, it is ignored."""
    if tool is not None:
        fields["tool"] = tool[:MAX_LOGGED_TOOL_NAME]
    if cause is not None:
        cls = error_class or classify_error(cause)
        lowered = cls == "misconfigured" and level is not None and level < level_for(cls)
        want = level if lowered else level_for(cls)
        if not already_logged(cause, want):
            # Marks it (an outer handler's line then drops to DEBUG); the stack once, at ERROR.
            log_failure(
                logger, "tool_call.end", cause, level=want, error_class=cls, include_error_text=False,
                outcome=outcome, reason=reason, **fields,
            )
            return
        # Already logged (another call failed with the same exception): this call's line, at its level, no second stack.
        log_event(
            logger, "tool_call.end", level=want, outcome=outcome, reason=reason, error_class=cls,
            error_type=type(cause).__name__, **fields,
        )
        return
    if outcome == "failure":
        level = level_for(error_class) if error_class else logging.WARNING
    else:
        level = logging.DEBUG if reason == "awaiting_approval" else logging.INFO
    log_event(logger, "tool_call.end", level=level, outcome=outcome, reason=reason, error_class=error_class, **fields)


@dataclass
class _Ended:
    """How a call ended, filled in by `_dispatch` at its exit; a raise leaves the defaults."""

    outcome: str = "failure"
    reason: str | None = "exception"
    output: ToolOutput | None = None
    duration_ms: int | None = None
    deny_reason: str | None = None


def collapse_repeated_name(name: str) -> str | None:
    """If *name* is a shorter string repeated back-to-back 2+ times (e.g.
    ``"knowledgegraph__searchknowledgegraph__search"`` ->
    ``"knowledgegraph__search"``), returns the shortest repeating unit;
    otherwise ``None``.

    Some models/gateways degenerate into echoing an already-hallucinated
    tool name doubled, then quadrupled, on successive turns once a bad name
    has entered conversation history (each failed call's name becomes part
    of the next request's context). Left unchecked this both keeps failing
    forever AND grows the name past provider-side length limits (observed:
    OpenAI's 128-char function-name cap) until the whole request is
    rejected outright. Trying the collapsed form here — before giving up
    with an "Unknown tool" error — often turns the call into one that
    resolves correctly on its very first occurrence.
    """
    length = len(name)
    if length < 2:
        return None
    for unit_len in range(1, length // 2 + 1):
        if length % unit_len:
            continue
        unit = name[:unit_len]
        if unit * (length // unit_len) == name:
            return unit
    return None


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
        invocation_journal: "InvocationJournal | None" = None,
        bounds: ToolTimeBounds | None = None,
        rate_limits: RateLimitBudget | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._registry = registry
        self._bounds = bounds or ToolTimeBounds()
        self._rate_limits = rate_limits or RateLimitBudget()
        self._sleep = sleep
        self._kernel = kernel or HookRegistry()
        # See `transport/opik_tracing.py::maybe_start_tool_span` — set from
        # `AgentRuntime.opik_enabled` by `Agent.__init__` so every tool call
        # (including special routes dispatched via `override_execute`) gets
        # an Opik "tool" span nested under the active agent/LLM span.
        self._opik_enabled = opik_enabled
        self._opik_project_name = opik_project_name
        self._invocation_journal = invocation_journal

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
        on_pending: OnPending | None = None,
        scope: "ToolScope | None" = None,
        unknown_effects: bool = False,
        retry_rate_limited: bool = False,
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

        `on_pending`, when given, is awaited with `(call, pending)` if PRE_TOOL_USE left the
        call for a person to approve later (`ToolCallContext.ask_later`). The call isn't
        executed; the result carries the pending message. Without `on_pending` it is an
        ordinary ASK.

        `scope`, when given (always set by the real turn loop; `None` in
        standalone/test calls), is attached to both decision contexts so
        middleware can reach `ctx.scope.turn.run` for ambient run state —
        see `core/scope.py`.

        `unknown_effects`, set by a caller that records effects (the durable
        tool port, for write/execute calls): a failure after a send the
        client layer could not confirm (`harness_core.effects.ambiguity`), or
        an `EffectUnknown` the tool raised, raises `EffectUnknown` instead of
        becoming an error result, so the effect log records `unknown` and a
        re-call is never executed again. Legacy callers keep error results.

        `retry_rate_limited`, set by a caller that knows the call is a read: a
        `rate_limited` failure is tried again after the service's retry-after
        within the call's `RateLimitBudget`; past it, the failure is the answer.

        Logs one `tool_call.end` per call, with `tool` and `tool_call_id` (and the tool's
        `log_ids`) bound on every line logged while it runs.
        """
        malformed = malformed_call_error(call.name, call.arguments)
        if malformed is not None:
            # The converter could not parse the model's arguments (CH-11): corrected before any hook, as legacy's tool
            # loop does, so neither loop runs the tool or counts it as the tool's own failure.
            log_tool_call_end(
                outcome="failure", reason="malformed_args", error_class="bad_request",
                tool=call.name, tool_call_id=call.id,
            )
            return CoreToolResult(
                tool_call_id=call.id, name=call.name, content=malformed, is_error=True, error_code="invalid_call"
            )
        resolved_name = self.resolve_tool_name(call.name)
        tool = self._registry.resolve_by_name(resolved_name) if self._registry.has(resolved_name) else None
        ids = tool.log_ids if tool is not None else {}
        ended = _Ended()
        with log_fields(
            tool=resolved_name[:MAX_LOGGED_TOOL_NAME], tool_call_id=call.id,
            mcp_server_id=ids.get("mcp_server_id"), toolset_id=ids.get("toolset_id"),
        ):
            try:
                result = await self._dispatch(
                    call, resolved_name, ended, session_id=session_id, caller=caller,
                    override_execute=override_execute, on_denied=on_denied, on_ask=on_ask,
                    on_pending=on_pending, scope=scope, unknown_effects=unknown_effects,
                    retry_rate_limited=retry_rate_limited,
                )
            except BaseException as exc:
                self._log_end(ended, tool, raised=exc)
                raise
            self._log_end(ended, tool)
        return result

    @staticmethod
    def _log_end(ended: _Ended, tool: "Tool | None", *, raised: BaseException | None = None) -> None:
        output = None if raised is not None else ended.output
        fields = {key: output.end_fields.get(key) for key in _END_FIELD_KEYS} if output is not None else {}
        cause = raised or (output.cause if output is not None and ended.outcome == "failure" else None)
        log_tool_call_end(
            outcome=ended.outcome, reason=ended.reason, cause=cause, deny_reason=ended.deny_reason,
            level=output.end_fields.get("level") if output is not None else None,
            kind=tool.log_kind if tool is not None else None, duration_ms=ended.duration_ms, **fields,
        )

    async def _dispatch(
        self,
        call: ToolCall,
        resolved_name: str,
        ended: _Ended,
        *,
        session_id: str | None,
        caller: str,
        override_execute: OverrideExecute | None,
        on_denied: Callable[[str], Awaitable[None]] | None,
        on_ask: OnAsk | None,
        on_pending: OnPending | None,
        scope: "ToolScope | None",
        unknown_effects: bool,
        retry_rate_limited: bool,
    ) -> CoreToolResult:
        path = self._registry.path_for_name(resolved_name)
        tags = self._registry.tags_for_name(resolved_name) if path is not None else ()
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

        pending = pre_ctx.pending_approval if pre_ctx.decision == PreDecision.ASK else None
        if pending is not None and on_pending is not None:
            await on_pending(call, pending)
            ended.outcome, ended.reason = "skipped", "awaiting_approval"
            return CoreToolResult(tool_call_id=call.id, name=call.name, content=pending.message, is_error=True)

        approved = pre_ctx.decision == PreDecision.ALLOW
        if pre_ctx.decision == PreDecision.ASK:
            approved = await on_ask(call, pre_ctx.decision_reason or "") if on_ask is not None else False

        if not approved:
            reason = pre_ctx.decision_reason or f"tool call to {call.name!r} was not approved"
            if on_denied is not None:
                await on_denied(reason)
            ended.outcome, ended.reason = "skipped", "denied"
            ended.deny_reason = pre_ctx.decision_code
            return CoreToolResult(tool_call_id=call.id, name=call.name, content=reason, is_error=True)

        from harness_core.models.transports.opik_tracing import (
            maybe_start_tool_span,
            record_tool_span_output,
        )

        invocation = None
        if self._invocation_journal is not None and scope is not None:
            invocation = await self._invocation_journal.begin(
                run_id=scope.turn.run.identity.run_id,
                call_id=call.id,
                tool_name=resolved_name,
                tool_path=resolved_path,
                arguments=pre_ctx.tool_input,
                tags=tags,
            )

        if invocation is not None and invocation.cached_result is not None:
            cached = invocation.cached_result
            # Recovery repeats the recorded result; the call itself ran (and logged) before.
            ended.outcome, ended.reason = "skipped", "replayed"
            # Current records store the fully post-processed CoreToolResult so
            # recovery repeats neither the external effect nor POST_TOOL_USE
            # side effects such as artifact registration. Accept the original
            # pre-release record shape as a migration aid for local installs.
            if "tool_call_id" in cached:
                return CoreToolResult.model_validate(cached)
            return CoreToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=(
                    cached.get("data")
                    if cached.get("success")
                    else (cached.get("error") or "tool execution failed")
                ),
                is_error=not bool(cached.get("success")),
                sources=[
                    Source.model_validate(source)
                    for source in cached.get("sources", [])
                ],
            )

        try:
            with maybe_start_tool_span(
                enabled=self._opik_enabled,
                name=call.name,
                arguments=dict(call.arguments),
                project_name=self._opik_project_name,
            ) as span:
                started = time.perf_counter()
                try:
                    tool_result = _without_secrets(await self._guarded_run(
                        call,
                        resolved_name,
                        pre_ctx.tool_input,
                        override_execute,
                        unknown_effects=unknown_effects,
                        retry_rate_limited=retry_rate_limited,
                    ))
                finally:
                    ended.duration_ms = round((time.perf_counter() - started) * 1000)
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
            ended.output = final
            if post_ctx.decision == PostDecision.BLOCK:
                reason = post_ctx.decision_reason or "tool result was blocked"
                ended.reason = "blocked"
                final_result = CoreToolResult(
                    tool_call_id=call.id,
                    name=call.name,
                    content=reason,
                    is_error=True,
                )
            else:
                ended.outcome = "success" if final.success else "failure"
                ended.reason = None if final.success else final.end_fields.get("reason", "tool_error")
                final_result = CoreToolResult(
                    tool_call_id=call.id,
                    name=call.name,
                    content=(
                        final.data
                        if final.success
                        else (final.error or "tool execution failed")
                    ),
                    is_error=not final.success,
                    sources=list(final.sources),
                    artifact_meta=post_ctx.metadata.get("artifact_meta"),
                    error_code=None if final.success else (final.error_code or "failed"),
                    retry_after_s=None if final.success else final.retry_after_s,
                )

            if invocation is not None:
                await self._invocation_journal.finish(  # pyright: ignore[reportOptionalMemberAccess]
                    invocation.invocation_id,
                    result=final_result.model_dump(mode="json"),
                    success=not final_result.is_error,
                )
            return final_result
        except BaseException:
            if invocation is not None:
                await self._invocation_journal.interrupted(  # pyright: ignore[reportOptionalMemberAccess]
                    invocation.invocation_id
                )
            raise

    def resolve_tool_name(self, name: str) -> str:
        """Canonicalizes *name* to the tool name that will actually execute,
        applying the same repeated-name-collapse / toolset-group-expansion
        fallback `_run` falls back on when an exact match fails. Returns
        *name* unchanged if no unambiguous match is found.

        `call_tool` calls this BEFORE building `ToolCallContext` so
        PRE_TOOL_USE authorizes against the resolved tool's real path/tags
        — resolving only in `_run`, after authorization already ran against
        the raw, unresolved name, would let allow/deny-list, risk-tag, and
        destructive-command-pattern middleware (see `hooks/middleware/
        builtin/{permission,mode,tool_safety}.py`, all of which key off
        `ctx.tool_path`/`ctx.tags`) apply to the wrong tool entirely, or
        silently skip a check that should have applied to the tool that
        actually runs.
        """
        if self._registry.has(name):
            return name
        collapsed = collapse_repeated_name(name)
        if collapsed is not None and collapsed != name and self._registry.has(collapsed):
            return collapsed
        # The model addressed a toolset by its group name (e.g. a
        # `KnowledgeHub`-style toolset with one tool, called as
        # `"knowledgehub"` instead of `"knowledgehub__list_files"`) instead
        # of the concrete `app__tool` name. `expand_tool_names` already
        # knows how to turn a group/prefix name into its member tool names
        # for schema listing (see `registry.py`) — reuse it here so a call
        # that unambiguously means one tool still succeeds instead of
        # round-tripping an "unknown tool" error back to the model.
        candidates = self._registry.expand_tool_names([name])
        if len(candidates) == 1:
            return candidates[0]
        return name

    def unknown_tool_error(
        self, name: str, *, callable_: Callable[[str], bool] | None = None
    ) -> str:
        """The error for a call *name* does not resolve: an ambiguous toolset
        name lists its tools (those ``callable_`` keeps, when given), anything
        else is a plain unknown tool."""
        candidates = self._registry.expand_tool_names([name])
        if callable_ is not None:
            candidates = [c for c in candidates if callable_(c)]
        if candidates:
            return (
                f"{UNKNOWN_TOOL_ERROR_PREFIX} {name!r} — that's a toolset name, not a "
                f"callable tool. Call one of its tools directly: {', '.join(sorted(candidates))}"
            )
        return f"{UNKNOWN_TOOL_ERROR_PREFIX} {name}"

    async def _guarded_run(
        self,
        call: ToolCall,
        resolved_name: str,
        tool_input: dict,
        override_execute: OverrideExecute | None,
        *,
        unknown_effects: bool,
        retry_rate_limited: bool = False,
    ) -> ToolOutput:
        """``_run`` within the call's time bounds, and for a caller that records
        effects: a failure after a send the client layer could not confirm, or a
        call stopped at a bound, is ``EffectUnknown``. ``_run`` keeps its
        signature (test seams replace it)."""
        if not unknown_effects:
            bounded = await self._bounded(
                call, resolved_name, tool_input, override_execute, retry_rate_limited=retry_rate_limited
            )
            return self._settle_bound(call, bounded, unknown_effects=False)
        with track_sends() as sends:
            try:
                bounded = await self._bounded(call, resolved_name, tool_input, override_execute)
            except EffectUnknown:
                raise
            except Exception as exc:
                if sends.ambiguous:
                    raise unknown_effect(sends, type(exc).__name__) from exc
                raise
        output = self._settle_bound(call, bounded, unknown_effects=True)
        if not output.success and sends.ambiguous:
            raise unknown_effect(sends, "tool reported an error")
        return output

    async def _bounded(
        self,
        call: ToolCall,
        resolved_name: str,
        tool_input: dict,
        override_execute: OverrideExecute | None,
        *,
        retry_rate_limited: bool = False,
    ) -> ToolOutput | BoundExceeded:
        try:
            tool = self._registry.resolve_by_name(resolved_name)
        except ToolNotFoundError:
            tool = None
        declared = tool.time_bound_s(tool_input) if tool is not None else "default"
        wall_s = self._bounds.wall_for(declared)
        # The tool's dependency reads see what is left of this bound (ERR-UX: a black-holed store fails fast).
        with call_deadline(wall_s):
            return await run_bounded(
                lambda: self._run_retrying(call, resolved_name, tool_input, override_execute, retry_rate_limited),
                tool=call.name,
                wall_s=wall_s,
                stall_s=self._bounds.stall_s,
            )

    async def _run_retrying(
        self,
        call: ToolCall,
        resolved_name: str,
        tool_input: dict,
        override_execute: OverrideExecute | None,
        retry_rate_limited: bool,
    ) -> ToolOutput:
        """``_run``, tried again while the service rate limits a read and the budget pays the wait (TG-08)."""
        budget = self._rate_limits
        retries, waited = 0, 0.0
        while True:
            output = await self._run(call, resolved_name, tool_input, override_execute)
            if not retry_rate_limited or output.success or output.error_code != "rate_limited":
                return output
            wait = budget.wait_s(output.retry_after_s, retries)
            if not budget.affords(wait, retries=retries, waited_s=waited, left_s=remaining_s()):
                logger.info(
                    "tool.rate_limited choice=answer tool=%s retries=%d waitedS=%g retryAfterS=%s",
                    call.name, retries, waited, output.retry_after_s,
                )
                return output
            retries += 1
            waited += wait
            logger.info("tool.rate_limited choice=wait tool=%s retry=%d waitS=%g", call.name, retries, wait)
            await self._sleep(wait)

    def _settle_bound(
        self, call: ToolCall, outcome: ToolOutput | BoundExceeded, *, unknown_effects: bool
    ) -> ToolOutput:
        if isinstance(outcome, ToolOutput):
            return outcome
        logger.warning(
            "tool.time_bound_exceeded reason=%s tool=%s boundS=%g callId=%s",
            outcome.reason, outcome.tool, outcome.bound_s, call.id,
        )
        if unknown_effects:
            raise EffectUnknown(outcome.message)
        return ToolOutput(
            success=False, error=outcome.message, error_code="timeout",
            end_fields={"reason": "time_bound_exceeded", "error_class": "timeout"},
        )

    async def _run(
        self,
        call: ToolCall,
        resolved_name: str,
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
                    error_code=known_code(core_result.error_code),
                    retry_after_s=core_result.retry_after_s,
                    cause=core_result.cause,
                    end_fields=core_result.end_fields,
                )
            tool = self._registry.resolve_by_name(resolved_name)
        except ToolNotFoundError:
            # `resolved_name` (from `resolve_tool_name`, called earlier in
            # `call_tool`) already reflects the best available match.
            return ToolOutput(
                success=False, error=self.unknown_tool_error(call.name), error_code="invalid_call",
                end_fields=_UNKNOWN_TOOL,
            )

        try:
            normalized = dict(tool_input)
            tool.validate(normalized)
        except ToolValidationError as exc:
            # A bare exception string ("missing required argument 'code'")
            # gives a weak model nothing to correct against beyond guessing
            # again — appending the tool's own parameter list lets it fix
            # the call in one turn instead of flailing until
            # `ToolErrorTracker`'s 3-strike limit blocks the tool outright.
            return ToolOutput(
                success=False, error=f"{exc}\n\n{_usage_hint(tool)}", error_code="invalid_call",
                end_fields={"reason": "bad_args", "error_class": "bad_request"},
            )
        except Exception as exc:
            return ToolOutput(success=False, error=str(exc), error_code="invalid_call", cause=exc, end_fields=_EXCEPTION)

        try:
            return await tool.execute(**normalized)
        except EffectUnknown as exc:
            # A tool's own "may have happened" is an ambiguous send of this call.
            note_ambiguous_send(type(exc).__name__)
            return ToolOutput(success=False, error=str(exc), cause=exc, end_fields=_EXCEPTION)
        except Exception as exc:
            return ToolOutput(
                success=False, error=str(exc), error_code=tool_error_code(exc), retry_after_s=retry_after_s(exc),
                cause=exc, end_fields=_EXCEPTION,
            )

    async def execute(self, call: ToolCall) -> CoreToolResult:
        """Backward-compatible alias for `call_tool` with no session/override."""
        return await self.call_tool(call)


def _without_secrets(output: ToolOutput) -> ToolOutput:
    """A failed call's text as the model, the tool card, the saved transcript and the trace get it: an API's own words
    stay (the model needs them to correct its call), a key, token or password in them is masked."""
    if output.success:
        return output
    return replace(output, error=_masked(output.error), data=_masked(output.data))


def _masked(value: object) -> object:
    if isinstance(value, str):
        return redact_secrets_in_text(value)
    if isinstance(value, Mapping):
        return {key: _masked_field(key, item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_masked(item) for item in value]
    return value


def _masked_field(key: object, value: object) -> object:
    # An opaque value has no shape the text masker knows (`{"password": "hunter2"}`): its key says what it is, by the
    # same name rule the log redactor uses. Booleans, numbers and None describe a credential; they stay.
    if isinstance(key, str) and not isinstance(value, (bool, int, float, type(None))):
        mask = credential_value_mask(key)
        if mask is not None:
            return mask
    return _masked(value)
