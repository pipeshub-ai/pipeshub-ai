from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from app.agent_loop_lib.agent import observability as obs
from app.agent_loop_lib.core.messages import (
    MALFORMED_TOOL_CALL_ARGS_KEY,
    MALFORMED_TOOL_CALL_ERROR_KEY,
)
from app.agent_loop_lib.core.scope import ToolScope
from app.agent_loop_lib.core.types import Artifact, Confidence, Goal, Message, ToolCall, ToolResult
from app.agent_loop_lib.events.base import EventType, ToolCallStatus
from app.agent_loop_lib.tools.special_route import RouteContext, SpecialRouteRegistry
from app.agent_loop_lib.tools.tags import TAG_DEDUP_EXACT, TAG_LIFECYCLE_TERMINAL

# Internal tools whose execution should not produce frontend-visible events.
# The tool still runs and its result enters the conversation; only the
# TOOL_CALL / TOOL_RESULT emissions are skipped.
_NO_EMIT_TOOLS: frozenset[str] = frozenset({"retrieve_artifact_content"})

if TYPE_CHECKING:
    from app.agent_loop_lib.agent.spec import AgentSpec
    from app.agent_loop_lib.core.scope import TurnScope
    from app.agent_loop_lib.core.tool_schema import ToolSchema
    from app.agent_loop_lib.runtime.runtime import AgentRuntime
    from app.agent_loop_lib.tools.builtin.planning.task_complete import TaskCompletionOutcome
    from app.agent_loop_lib.tools.registry import ToolRegistry


@runtime_checkable
class TerminalTool(Protocol):
    """Structural type for any `Tool` tagged `TAG_LIFECYCLE_TERMINAL` — its
    own successful result carries the loop's stop signal. `extract_outcome`
    post-processes THIS tool's result shape into a `TaskCompletionOutcome`,
    so the turn loop dispatches on the tag rather than hardcoding
    `call.name == "task_complete"` — a future terminal tool just needs the
    tag plus this method, no edit to `execute_tool_call` below."""

    def extract_outcome(
        self, tr: ToolResult, call: ToolCall, fallback_text: str
    ) -> "TaskCompletionOutcome": ...

"""Per-tool-call dispatch, extracted from `Agent.step()`.

Handles exactly one `ToolCall`: duplicate-search detection, looking up a
`SpecialRouteHandler` for tools that need Agent-level state (spawn_agent,
best_of_n, clarify, replan, handoff, write_todos, fetch_tools — see
`tools/special_route.py`), and delegating the whole resolve -> PreToolUse
-> execute -> PostToolUse sequence to `ToolExecutor.call_tool()` — one call
per tool call, regardless of which route it takes, so permission/mode/
approval/audit-log middleware apply uniformly.
"""


def compute_duplicate_flags(
    calls: list[ToolCall], seen_tool_calls: set[str], registry: "ToolRegistry"
) -> dict[str, bool]:
    """Synchronous pre-pass over one `asyncio.gather()` wave of calls, run
    BEFORE any of them starts its own coroutine — so `seen_tool_calls` is
    fully updated before any call's `await` points exist.

    This check-then-add used to live inline inside `execute_tool_call`,
    AFTER that function's own `await agent.emit(...)`. Two identical
    calls gathered into the same wave could both reach the check before
    either had added its own signature, and both would execute. Doing the
    whole check-then-add loop here, synchronously, makes that interleaving
    structurally impossible.

    Only calls to a tool tagged `TAG_DEDUP_EXACT` (builtin `web_search`/
    `web_scrape`, or any host-defined tool that opts in) are ever flagged
    — every OTHER repeated call still updates `seen_tool_calls` for
    bookkeeping, but is never treated as a duplicate, since a non-idempotent
    tool repeated on purpose (e.g. re-reading a file after writing it)
    must still run.
    """
    flags: dict[str, bool] = {}
    for call in calls:
        call_sig = f"{call.name}:{json.dumps(call.arguments, sort_keys=True)}"
        is_dedupable = TAG_DEDUP_EXACT in registry.tags_for_name(call.name)
        flags[call.id] = call_sig in seen_tool_calls and is_dedupable
        seen_tool_calls.add(call_sig)
    return flags


def _resolve_quietly(agent, name: str):
    """Best-effort tool lookup for summarization only — never raises. A
    name that doesn't resolve (special routes, malformed-call sentinel
    names, mid-registration edge cases) just means "no tool-declared
    summarizer available", not an error."""
    try:
        return agent._executor.registry.resolve_by_name(name)
    except Exception:
        return None


def _args_summary(agent, runtime: "AgentRuntime", call: ToolCall) -> str | None:
    """Precedence: the resolved tool's own declared summarizer (`@tool(
    args_summary=...)` — see `tools/decorators.py`) first, since that's
    colocated with the tool definition and always the most specific;
    `runtime.summarizer` (the platform-owned registry for tools that
    aren't defined via `@tool`, e.g. dynamic per-request adapters) next;
    `None` otherwise. Every layer is fail-safe — `Tool.summarize_args`'s
    default implementations already swallow formatter exceptions, but
    this function must never raise either, since a missing summary is
    always a safe fallback (emitters/`TranscriptCollector`/the frontend
    treat it as "use the raw preview") while an exception here would not
    be."""
    tool = _resolve_quietly(agent, call.name)
    if tool is not None:
        try:
            summary = tool.summarize_args(call.arguments)
            if summary is not None:
                return summary
        except Exception:
            pass
    if runtime.summarizer is None:
        return None
    try:
        return runtime.summarizer.summarize_args(call.name, call.arguments)
    except Exception:
        return None


def _result_summary(agent, runtime: "AgentRuntime", call: ToolCall, tr: ToolResult) -> str | None:
    """Same precedence and fail-safe contract as `_args_summary`. Must be
    called with the FULL `tr` (before any `[:200]` truncation) — that's
    the entire reason this lives in `tool_loop.py` rather than in an
    emitter, which only ever sees the already-truncated preview."""
    tool = _resolve_quietly(agent, call.name)
    if tool is not None:
        try:
            summary = tool.summarize_result(call.arguments, tr)
            if summary is not None:
                return summary
        except Exception:
            pass
    if runtime.summarizer is None:
        return None
    try:
        return runtime.summarizer.summarize_result(call.name, call.arguments, tr).result_summary
    except Exception:
        return None


@dataclass
class ToolCallOutcome:
    result: ToolResult
    task_done: bool = False
    final_output: object = None
    artifacts: list[Artifact] = None  # type: ignore[assignment]
    # Optional typed sub-agent output contract, mirrored from whatever
    # `TerminalTool.extract_outcome` returned — see `AgentResult`
    # (`core/types.py`) for what each field means; `None`/`[]` here just
    # means "this terminal call didn't set it", not an error.
    confidence: Confidence | None = None
    record_ids: list[str] = None  # type: ignore[assignment]
    needs_input: str | None = None

    def __post_init__(self) -> None:
        if self.artifacts is None:
            self.artifacts = []
        if self.record_ids is None:
            self.record_ids = []


def initial_visible_tools(spec: "AgentSpec", runtime: "AgentRuntime") -> set[str]:
    """Essentials + pinned toolsets, per the lazy-toolsets design tenet:
    only overviews + meta-tools + role-pinned essentials ship upfront.
    Anything not assigned to any toolset is an "essential" by definition.

    When `spec.tool_names` is non-empty, the result is intersected with it
    — a permission ceiling (see `AgentSpec.tool_disclosure`) can only ever
    narrow the starting visibility, never widen it beyond what the agent
    is actually allowed to call. Harmless when `tool_disclosure == "eager"`
    too: that branch of `tool_schemas_for_turn` never consults this set."""
    registry = runtime.tool_registry
    all_names = set(registry.names())
    grouped = registry.grouped_tool_names()
    essentials = all_names - grouped
    for toolset_name in spec.pinned_toolsets:
        essentials.update(registry.tools_in_toolset(toolset_name))
    if spec.tool_names:
        essentials &= set(spec.tool_names)
    return essentials


def tool_schemas_for_turn(agent, spec: "AgentSpec", runtime: "AgentRuntime") -> list["ToolSchema"]:
    """Which tool schemas the model sees this turn — all of them, unless
    the registry opts into lazy toolsets, in which case it's essentials
    plus whatever `agent.visible_tools` has grown to via fetch_tools."""
    registry = runtime.tool_registry
    if registry is None:
        return []
    if not registry.has_toolsets():
        return registry.schemas(spec.tool_names or None)

    if agent.visible_tools is None:
        agent.visible_tools = initial_visible_tools(spec, runtime)

    if spec.tool_names:
        if spec.tool_disclosure == "lazy":
            # tool_names is a permission CEILING here, not an eager grant:
            # visibility starts at essentials/pinned (already intersected
            # with the ceiling above) and only grows via fetch_tools/
            # search_tools/preloading — all of which enforce the same
            # ceiling (see lazy_toolsets.py's `_grant_set` usage) — so this
            # intersection is a defensive no-op in the common case, not the
            # only enforcement point.
            return registry.schemas(sorted(agent.visible_tools & set(spec.tool_names)))
        # eager (default): the explicit tool grant is ALL named tools,
        # fully visible from the start. Lazy disclosure (grouping tools
        # into toolsets and hiding them until fetch_tools loads them) only
        # applies to tools BEYOND what the spec explicitly lists. Without
        # this, a child agent spawned with a focused tool list (e.g.
        # ['web_search', 'web_scrape', 'task_complete']) would be
        # permanently locked out of grouped tools it was explicitly given,
        # since it lacks the list_toolsets/fetch_tools meta-tools needed
        # to discover and unlock them.
        return registry.schemas(spec.tool_names)

    return registry.schemas(list(agent.visible_tools))


async def execute_tool_call(
    agent,
    call: ToolCall,
    spec: "AgentSpec",
    runtime: "AgentRuntime",
    goal: Goal,
    messages: list[Message],
    turn_index: int,
    started_at: str,
    is_duplicate: bool,
    response_msg: Message,
    turn_scope: "TurnScope",
) -> ToolCallOutcome:
    """Handles exactly one `ToolCall`. `turn_scope` (created once per
    `Agent.step()` call) is used to build this call's `ToolScope` — carrying
    the tool identity plus the FULL scope chain (`.turn.run...`) down into
    both the PreToolUse/PostToolUse decision contexts (via `ToolExecutor.
    call_tool(scope=...)`) and special-route handlers (via `RouteContext.
    scope`). `messages` (already a fresh `context.messages()` snapshot taken
    by `Agent.step()` AFTER the assistant's response was recorded) becomes
    `ToolScope.messages` — see `core/scope.py` for why that snapshot timing
    is load-bearing for `clarify`'s HIL checkpoint.

    `is_duplicate` is computed by `compute_duplicate_flags()` in a
    synchronous pre-pass over the whole wave, BEFORE any call in the wave
    starts running — see that function's docstring for why the check can't
    safely live in here, after this coroutine's own `await` points."""
    _silent = call.name in _NO_EMIT_TOOLS
    if not _silent:
        await agent.emit(EventType.TOOL_CALL, {
            "tool": call.name, "args": call.arguments, "tool_call_id": call.id,
            "args_summary": _args_summary(agent, runtime, call),
        })

    # A call whose argument JSON couldn't be parsed (or repaired) by the
    # transport's message converter arrives here carrying these sentinel
    # keys instead of real arguments (see `app/agents/agent_loop/
    # converters.py::_recover_invalid_tool_call`). Short-circuit straight
    # to a corrective error `ToolMessage` — never resolve/validate/execute
    # a tool against sentinel data, and never let this look like a plain
    # no-tool-call turn (which `Agent.step()` would otherwise treat as a
    # successful, silent completion).
    if MALFORMED_TOOL_CALL_ARGS_KEY in call.arguments:
        raw_args = call.arguments.get(MALFORMED_TOOL_CALL_ARGS_KEY, "")
        parse_error = call.arguments.get(MALFORMED_TOOL_CALL_ERROR_KEY, "malformed JSON arguments")
        content = (
            f"Your call to `{call.name}` had invalid arguments: {parse_error}. "
            "Re-issue this exact tool call with syntactically valid JSON arguments "
            "(no markdown code fences, no trailing commas, no comments). "
            f"Arguments received (truncated): {str(raw_args)[:300]!r}"
        )
        tr = ToolResult(tool_call_id=call.id, name=call.name, content=content, is_error=True)
        if not _silent:
            await agent.emit(EventType.TOOL_RESULT, {
                "tool": tr.name, "is_error": True, "content": content[:200], "tool_call_id": call.id,
                "result_summary": _result_summary(agent, runtime, call, tr),
                "status": ToolCallStatus.ERROR,
            })
        return ToolCallOutcome(result=tr)

    # Repeated tool calls (same tool + same key argument) were already
    # detected by `compute_duplicate_flags()` — a synchronous pre-pass over
    # the whole wave, run before any call in it (including this one)
    # started. See that function's docstring for why detection can't
    # safely happen here, after this coroutine's own `await` above.
    if is_duplicate:
        tr = ToolResult(
            tool_call_id=call.id, name=call.name,
            content=(
                "[Duplicate call skipped — you already ran this exact search/scrape. "
                "Use the results you already have or call task_complete to finish.]"
            ),
            is_error=False,
        )
        if not _silent:
            await agent.emit(EventType.TOOL_RESULT, {
                "tool": tr.name, "is_error": tr.is_error,
                "content": str(tr.content)[:200], "tool_call_id": call.id,
                "result_summary": _result_summary(agent, runtime, call, tr),
                "status": ToolCallStatus.ERROR if tr.is_error else ToolCallStatus.SUCCESS,
            })
        return ToolCallOutcome(result=tr)

    # --- Checkpoint: PRE_TOOL ---
    await obs.save_checkpoint(agent, "pre_tool", goal, messages, turn_index, current_tool=call.name)

    # `tool_path` starts empty — `ToolExecutor.call_tool()` resolves the
    # real path/tags and fills them onto this SAME scope object before
    # dispatching PRE_TOOL_USE, so by the time `override_execute()` below
    # actually runs (from inside `_run()`, after PRE_TOOL_USE), `tool_scope.
    # tool_path`/`.tags` are already correct for handlers that read them.
    tool_scope = ToolScope(turn=turn_scope, call=call, tool_path="", messages=messages)

    handler = SpecialRouteRegistry(runtime.tool_registry).get(call.name)
    override_execute = None
    if handler is not None:
        ctx = RouteContext(agent=agent, scope=tool_scope)

        async def override_execute() -> ToolResult:
            return await handler.handle(call, ctx)
    else:
        # --- Normal tool execution ---
        await obs.write_state(agent, goal, "running_tool", turn_index=turn_index, started_at=started_at, current_tool=call.name)
        await obs.append_timeline(agent, "tool_call", f"Calling tool: {call.name}", "running_tool", {"tool": call.name, "args": call.arguments})

    blocked_reason: str | None = None

    async def _on_denied(reason: str) -> None:
        nonlocal blocked_reason
        blocked_reason = reason
        await agent.emit(EventType.TOOL_BLOCKED, {
            "tool": call.name, "reason": reason, "tool_call_id": call.id,
            "status": ToolCallStatus.BLOCKED,
        })
        await obs.append_timeline(
            agent, "tool_blocked", f"Tool blocked: {call.name} — {reason}", "running_tool",
            {"tool": call.name, "args": call.arguments, "reason": reason},
        )

    async def _on_ask(asked_call: ToolCall, reason: str) -> bool:
        return await obs.handle_tool_approval(agent, asked_call, reason, goal, messages, turn_index)

    tr = await agent._executor.call_tool(
        call, session_id=agent.session_id, override_execute=override_execute,
        on_denied=_on_denied, on_ask=_on_ask, scope=tool_scope,
    )

    # --- Record budget tool call ---
    if runtime.budget is not None:
        await runtime.budget.record_tool_call()

    if blocked_reason is not None:
        return ToolCallOutcome(result=tr)

    if not _silent:
        result_event: dict[str, Any] = {
            "tool": tr.name, "is_error": tr.is_error,
            "content": str(tr.content)[:200], "tool_call_id": call.id,
            "result_summary": _result_summary(agent, runtime, call, tr),
            "status": ToolCallStatus.ERROR if tr.is_error else ToolCallStatus.SUCCESS,
        }
        if tr.artifact_meta is not None:
            result_event["artifact_id"] = tr.artifact_meta.artifact_id
        await agent.emit(EventType.TOOL_RESULT, result_event)

    if tr.sources:
        await obs.append_timeline(
            agent, "tool_result_sources", f"Sources from {call.name}", "running_tool",
            {"tool": call.name, "is_error": tr.is_error, "sources": [s.model_dump() for s in tr.sources]},
        )

    # --- terminal-tool detection (TAG_LIFECYCLE_TERMINAL) ---
    if not tr.is_error and TAG_LIFECYCLE_TERMINAL in runtime.tool_registry.tags_for_name(call.name):
        tool = _resolve_quietly(agent, call.name)
        if isinstance(tool, TerminalTool):
            outcome = tool.extract_outcome(tr, call, agent.extract_text(response_msg))
            if outcome.error_result is not None:
                return ToolCallOutcome(result=outcome.error_result)
            return ToolCallOutcome(
                result=tr, task_done=outcome.task_done,
                final_output=outcome.final_output, artifacts=outcome.artifacts,
                confidence=getattr(outcome, "confidence", None),
                record_ids=getattr(outcome, "record_ids", None) or [],
                needs_input=getattr(outcome, "needs_input", None),
            )

    return ToolCallOutcome(result=tr)
