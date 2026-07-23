from __future__ import annotations

import asyncio
import datetime
import json
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.agent import hook_dispatch as hooks
from app.agent_loop_lib.agent import observability as obs
from app.agent_loop_lib.agent import resume as agent_resume
from app.agent_loop_lib.agent import streaming as agent_streaming
from app.agent_loop_lib.agent.loops import StepOutcome
from app.agent_loop_lib.agent.prompt import build_system_prompt
from app.agent_loop_lib.agent.tool_loop import (
    ToolCallOutcome,
    compute_duplicate_flags,
    execute_tool_call,
    tool_schemas_for_turn,
)
from app.agent_loop_lib.context.base import ContextBudget
from app.agent_loop_lib.core.context import RunContext
from app.agent_loop_lib.core.exceptions import AgentError, HookBlocked, RunCancelled
from app.agent_loop_lib.core.messages import AssistantMessage, ToolMessage, UserMessage
from app.agent_loop_lib.core.responses import RunUsage
from app.agent_loop_lib.core.scope import RunScope, TurnScope
from app.agent_loop_lib.core.streaming import (
    StreamCompleteEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
)
from app.agent_loop_lib.core.types import (
    AgentResult,
    AgentTurn,
    Artifact,
    Confidence,
    Goal,
    Message,
    MessageRole,
    Todo,
    ToolCall,
    ToolResult,
)
from app.agent_loop_lib.events.base import AgentEvent, EventType
from app.agent_loop_lib.hooks.middleware.builtin.turn_guards import install_turn_guards
from app.agent_loop_lib.tools.executor import ToolExecutor
from app.agent_loop_lib.tools.tags import TAG_SPAWN_BATCH

if TYPE_CHECKING:
    from app.agent_loop_lib.agent.spec import AgentSpec
    from app.agent_loop_lib.context.manager import ContextManager
    from app.agent_loop_lib.core.responses import ModelResponse
    from app.agent_loop_lib.models.base import Model
    from app.agent_loop_lib.runtime.runtime import AgentRuntime

# See Agent.emit — legacy event -> AG-UI-aligned event, fired alongside
# (never instead of) the legacy one. TOOL_BLOCKED counts as a TOOL_CALL_END
# too: a blocked call is a terminal outcome for that tool_call, same as a
# normal TOOL_RESULT.
_AG_UI_ALIASES: dict[EventType, EventType] = {
    EventType.AGENT_START: EventType.RUN_STARTED,
    EventType.AGENT_COMPLETE: EventType.RUN_FINISHED,
    EventType.ERROR: EventType.RUN_ERROR,
    EventType.TOOL_CALL: EventType.TOOL_CALL_START,
    EventType.TOOL_RESULT: EventType.TOOL_CALL_END,
    EventType.TOOL_BLOCKED: EventType.TOOL_CALL_END,
}


class Agent:
    """The fundamental agent: an `AgentSpec` (system prompt + tools + model
    + loop shape) bound to an `AgentRuntime` (shared services). Never
    subclassed — every behavioral difference between agents is a different
    `AgentSpec`/`AgentRuntime` combination, built by an `AgentFactory`,
    `AgentBuilder`, or by hand.

    `run(goal)` resolves the transport, then hands the entire turn loop to
    `spec.loop` (a `LoopStrategy`) — see `agent/loops.py`. Loop strategies
    drive the run by calling `step()` (this class's one, fixed,
    hook-instrumented turn primitive: PRE_TURN -> context shaping -> guarded
    model call -> tool dispatch -> POST_TURN) as many times as their shape
    needs, plus the small set of composition primitives below
    (`inject_user_message`, `last_tool_result`, `succeed`/`fail`) — nothing
    about `step()` itself changes per loop shape, which is what lets new
    loops (see `examples/`) be added with zero changes to this file.
    """

    def __init__(self, spec: "AgentSpec", runtime: "AgentRuntime", *, session_id: str | None = None) -> None:
        self._spec = spec
        self._runtime = runtime
        self._session_id = session_id

        # The Model is resolved lazily on first run() call, from
        # `spec.model.resolve(runtime.transport_registry)`, so that the
        # registry factory is not invoked at construction time. `Agent`
        # depends on the `Model` abstraction (Dependency Inversion) — never
        # on a concrete `LLMTransport` — so a differently-implemented
        # `Model` (a retry/cache/fallback decorator) can be substituted
        # without touching this class.
        self._model: "Model | None" = None
        # Cumulative usage across every LLM call this run has made so far —
        # the source of truth for callers that used to read
        # `agent.transport.total_input_tokens` off a concrete transport.
        self._usage = RunUsage()

        # ToolExecutor is always constructed — even with no `tool_registry`
        # configured (falls back to an empty one) — because special routes
        # (spawn_agent, clarify, best_of_n, replan, handoff, ...) dispatch
        # via `override_execute` and never need a real tool resolved, only
        # the PreToolUse/PostToolUse pipelines around them. Shares the
        # RUNTIME's single HookRegistry kernel, never a private one, so
        # PRE_TOOL_USE/POST_TOOL_USE middleware sees every tool call from
        # every agent sharing this runtime — including children spawned via
        # `AgentRuntime.run_child()`.
        self._executor = ToolExecutor(
            runtime.tool_registry,
            runtime.hooks,
            opik_enabled=runtime.opik_enabled,
            opik_project_name=runtime.opik_project_name,
        )
        self._hooks = self._executor.kernel

        # Deterministic per-turn guards every agent needs regardless of role
        # (budget/cancellation/deadline-warning/truncation-recovery) —
        # dispatched through the SAME kernel as PRE_TOOL_USE/POST_TOOL_USE,
        # idempotent per kernel instance (see hooks/middleware/builtin/
        # turn_guards.py). The confidence-gate/stall-detection guards are
        # deliberately NOT installed here — see that module's docstring —
        # opt in per role via `spec.middleware` below instead.
        install_turn_guards(
            self._hooks,
            budget=runtime.budget,
            cancellation_token=runtime.cancellation_token,
        )
        # Per-spec deterministic middleware (see AgentSpec.middleware's
        # docstring) — e.g. `require_critique()` bundled with a specific
        # agent definition regardless of which runtime ends up hosting it.
        for install in spec.middleware:
            install(self._hooks)

        self._run_ctx = RunContext(role_name=spec.name, model=spec.model.model)

        # Run-scoped conversation state — created fresh in run() unless a
        # caller (AgentRuntime.run_child, resume()) pre-seeds it.
        self._context = None

        # The per-`run()` ambient-state container (see `core/scope.py`) —
        # `None` until `run()` actually starts (this Agent may never have
        # run yet, or may be a throwaway instance `run_child()` is about to
        # configure). Everything that used to be a separate `Agent._xyz`
        # mutable field (`_todos`, `_visible_tools`, `_extra_prompt_
        # sections`, `_turns`, `_resume_turn_index`) now lives on this one
        # object instead — see the property forwarding below, which keeps
        # every existing `agent.todos`/`agent.visible_tools`/... call site
        # working unchanged. Deliberately NOT reset to `None` after `run()`
        # returns, so callers can still read `agent.todos`/`agent.run_ctx`/
        # etc. off a completed run (existing behavior).
        self._scope: RunScope | None = None
        # One-shot inheritance source: `AgentRuntime.run_child()` stashes
        # the spawning parent's `RunScope` here BEFORE calling `run()`; the
        # first `run()` call consumes and clears it (see `_inherit_from`).
        self._parent_scope: RunScope | None = None

        self._streaming: bool = False
        self._event_emitter_override = None
        self.last_stream_result: AgentResult | None = None

        # Async subagents: spawn_agent(detach=true) fires the child as a
        # fire-and-forget background task; tracked here purely to hold a
        # strong reference (asyncio only weakly retains scheduled tasks).
        self._detached_tasks: set = set()

        # Parallel batch spawns: when a turn has multiple spawn_agent calls,
        # step() pre-launches every one of them as a concurrent asyncio Task
        # here, keyed by ToolCall.id, BEFORE any of them reach
        # SpawnAgentTool.handle() — the handler just awaits its own slot.
        self._pending_spawn_tasks: dict[str, asyncio.Task] = {}

    # ---- AgentHandle surface (see tools/special_route.py) ----

    @property
    def spec(self) -> "AgentSpec":
        return self._spec

    @property
    def runtime(self) -> "AgentRuntime":
        return self._runtime

    @property
    def run_ctx(self) -> RunContext:
        return self._run_ctx

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def todos(self) -> list[Todo]:
        return self._scope.todos if self._scope is not None else []

    @todos.setter
    def todos(self, value: list[Todo]) -> None:
        if self._scope is not None:
            self._scope.todos = value

    @property
    def visible_tools(self) -> set[str] | None:
        return self._scope.visible_tools if self._scope is not None else None

    @visible_tools.setter
    def visible_tools(self, value: set[str] | None) -> None:
        if self._scope is not None:
            self._scope.visible_tools = value

    @property
    def context(self) -> "ContextManager | None":
        """This Agent's conversation `ContextManager` — `None` before the
        first `run()` call unless a caller pre-seeded it via
        `seed_context()`. Read-only surface for code that needs to append
        to or read the live conversation (e.g. `LoopStrategy` implementations
        injecting synthetic tool results) without reaching into `_context`
        directly."""
        return self._context

    def seed_context(self, context: "ContextManager") -> None:
        """Pre-seed this Agent's conversation context BEFORE calling
        `run()` — the documented extension point for callers that need
        `run()` to start from existing conversation history (e.g. previous
        turns reloaded from persistence) instead of the empty
        `ContextManager` `run()` creates on its own the first time
        `self._context is None`. `AgentRuntime.run_child()` and `resume()`
        use this same seam internally."""
        self._context = context

    @property
    def streaming(self) -> bool:
        """Whether this Agent is currently emitting per-token
        `TEXT_MESSAGE_*`/`REASONING_MESSAGE_*` deltas in addition to its
        normal lifecycle/tool events — true while driven via `Agent.
        stream()`, or when a caller opts a plain `run()` into the same
        behavior (e.g. `AgentRuntime.run_child(..., mirror_events=True)`,
        so a sub-agent's own turns fan into the SAME shared emitter the
        parent's events already flow through)."""
        return self._streaming

    @streaming.setter
    def streaming(self, value: bool) -> None:
        self._streaming = value

    @property
    def scope(self) -> RunScope | None:
        """This run's `RunScope`, once `run()` has started — `None` before
        the first `run()` call. Exposed (beyond the individual `todos`/
        `visible_tools`/... forwarding properties below) so callers that
        already hold a live `Agent` reference (e.g. `run_child()` callers
        wanting to pass `parent_scope=`) don't have to reach through a
        `ToolScope`/`TurnScope` chain that may not exist for them."""
        return self._scope

    @property
    def event_emitter(self):
        return self._event_emitter_override or self._runtime.event_emitter

    @property
    def model(self) -> "Model | None":
        """The resolved `Model` for this agent's `spec.model.provider` —
        `None` until the first `run()`/`run_from_message()` call resolves
        it lazily."""
        return self._model

    @property
    def usage(self) -> RunUsage:
        """Cumulative token usage across every LLM call this run has made
        so far — the source of truth for callers (e.g. the CLI) that used
        to read cumulative counters off a concrete transport instance."""
        return self._usage

    @property
    def started_at(self) -> str | None:
        return self._scope.started_at if self._scope is not None else None

    @property
    def max_turns(self) -> int:
        return self._spec.max_turns

    @property
    def start_turn_index(self) -> int:
        """Which turn index a `LoopStrategy` should start iterating from —
        0 for a fresh run, or the checkpoint's next turn after `resume()`."""
        return self._scope.resume_turn_index if self._scope is not None else 0

    def extract_text(self, msg: Message) -> str:
        """Plain text of `msg` — `AssistantMessage.text` (TextPart content
        only; thinking is deliberately excluded from user-facing output,
        same as the transports that parse it) for assistant messages, the
        raw string content for every other role."""
        if isinstance(msg, AssistantMessage):
            return msg.text
        if isinstance(msg.content, str):
            return msg.content
        return ""

    async def emit(self, event_type: EventType, payload: dict) -> None:
        emitter = self.event_emitter
        if emitter is None:
            return
        event = AgentEvent(event_type=event_type, run_context=self._run_ctx, payload=payload)
        await emitter.emit(event)

        # AG-UI alignment: fire the AG-UI-shaped equivalent ALONGSIDE the
        # legacy event, same payload, one translation table instead of
        # duplicating emit() calls at every call site.
        alias = _AG_UI_ALIASES.get(event_type)
        if alias is not None:
            await emitter.emit(AgentEvent(event_type=alias, run_context=self._run_ctx, payload=payload))

    # ---- composition primitives for LoopStrategy implementations ----

    async def inject_user_message(self, text: str, *, pinned: bool = False) -> None:
        """Append a USER message to this run's conversation — how a
        `LoopStrategy` steers the model between `step()` calls (a plan, a
        phase transition, a self-critique nudge) without reaching into
        context internals.

        `pinned=True` additionally exempts this message from
        `shape_sliding_window`'s eviction — for the rare injected message
        whose loss would break the loop's own control flow (a plan the
        rest of the run executes against), not just degrade guidance. See
        `UserMessage.pinned`."""
        await self._context.add(UserMessage(content=text, injected=True, pinned=pinned))

    def set_prompt_section(self, name: str, content: str | None) -> None:
        """Add/replace/remove one run-scoped system-prompt section (see
        `agent/prompt.py`). Prefer `inject_user_message` for steering the
        model turn-to-turn; this is for the rarer case of changing what the
        model is TOLD IT IS, not what it's being asked to do next."""
        if self._scope is None:
            return
        if content:
            self._scope.extra_prompt_sections[name] = content
        else:
            self._scope.extra_prompt_sections.pop(name, None)

    def last_assistant_text(self) -> str:
        """Text of the most recent assistant message across this run's
        turns so far, or "" if none."""
        turns = self._scope.turns if self._scope is not None else []
        for turn in reversed(turns):
            for msg in reversed(turn.messages):
                if msg.role == MessageRole.ASSISTANT:
                    text = self.extract_text(msg)
                    if text:
                        return text
        return ""

    def last_tool_result(self, name: str) -> Any:
        """The `.content` of the most recent result for a tool named `name`
        across this run's turns so far, or `None` if it hasn't been called
        yet. Lets a `LoopStrategy` react to e.g. `critique_plan`'s verdict
        without parsing the raw turn/message structure itself."""
        turns = self._scope.turns if self._scope is not None else []
        for turn in reversed(turns):
            for tr in reversed(turn.tool_results):
                if tr.name == name:
                    return tr.content
        return None

    def has_successful_tool_result(self, name: str) -> bool:
        """Whether the most recent turn that contained a result for tool
        `name` has at least one NON-ERROR result for it. Lets a
        `LoopStrategy` distinguish "all spawn_agent calls errored" (retry)
        from "at least one spawn ran" (proceed to verify)."""
        turns = self._scope.turns if self._scope is not None else []
        for turn in reversed(turns):
            results = [tr for tr in turn.tool_results if tr.name == name]
            if results:
                return any(not tr.is_error for tr in results)
        return False

    async def succeed(
        self,
        goal: Goal,
        output: Any,
        artifacts: list[Artifact] | None = None,
        *,
        event: str = "agent_complete",
        summary: str = "",
        detail: dict | None = None,
        confidence: Confidence | None = None,
        record_ids: list[str] | None = None,
        needs_input: str | None = None,
    ) -> AgentResult:
        """Build and persist a successful `AgentResult` from this run's
        recorded turns. The one, shared tail for every "run finished
        successfully" exit — used by `Agent.step()` itself (no-tool-calls
        exit, `task_complete` exit) and directly by loop strategies that
        want to end the run on their own terms (e.g. `SingleShotLoop`).

        `confidence`/`record_ids`/`needs_input` are the optional typed
        sub-agent output contract (see `AgentResult`, `core/types.py`) —
        `None`/`[]` when the terminal tool call didn't set them, same as
        every other caller of this method before this contract existed."""
        turns = self._scope.turns if self._scope is not None else []
        result = AgentResult(
            goal=goal, output=output, artifacts=artifacts or [], turns=list(turns),
            success=True, usage=self._usage,
            confidence=confidence, record_ids=record_ids or [], needs_input=needs_input,
        )
        await self.emit(EventType.AGENT_COMPLETE, {"output": output if not isinstance(output, str) else output[:200]})
        await obs.write_state(self, goal, "completed", turn_index=len(turns), started_at=self.started_at or _now())
        await obs.append_timeline(self, event, summary, "completed", detail or {})
        messages = await self._context.messages() if self._context else []
        await obs.save_checkpoint(self, "agent_complete", goal, messages, len(turns))
        await self._post_agent(result)
        return result

    async def fail(
        self,
        goal: Goal,
        error: str,
        *,
        event: str = "agent_failed",
        summary: str | None = None,
        detail: dict | None = None,
        status: str = "failed",
    ) -> AgentResult:
        """Build and persist a failed `AgentResult` — the shared tail for
        every "run stopped without succeeding" exit (blocked hooks,
        cancellation, transport errors, max_turns exhausted)."""
        turns = self._scope.turns if self._scope is not None else []
        result = AgentResult(goal=goal, turns=list(turns), success=False, error=error, usage=self._usage)
        await self.emit(
            EventType.CANCELLATION if status == "cancelled" else EventType.ERROR,
            {"error": error},
        )
        await obs.write_state(self, goal, status, turn_index=len(turns), started_at=self.started_at or _now())
        await obs.append_timeline(self, event, summary or error, status, detail or {"error": error})
        await self._post_agent(result)
        return result

    async def _post_agent(self, result: AgentResult) -> None:
        await hooks.dispatch_post_agent(self._hooks, result, scope=self._scope)

    # ---- run ----

    async def run_from_message(self, message: str) -> AgentResult:
        """Top-level entry from a raw user message: parse intent -> build a
        structured Goal -> check feasibility -> run(). A thin composition
        over `run()`, not a second Agent — the structured Goal's
        requirements/success_criteria/constraints already render into the
        system prompt's goal-brief section (see `agent/prompt.py`)."""
        from app.agent_loop_lib.agent.feasibility import FeasibilityChecker
        from app.agent_loop_lib.agent.goal import GoalBuilder
        from app.agent_loop_lib.agent.intent import IntentParser
        from app.agent_loop_lib.agent.single_shot_runner import build_task_complete_runtime

        if self._model is None:
            self._resolve_model()

        auxiliary_runtime = build_task_complete_runtime(
            self._runtime.transport_registry,
            opik_enabled=self._runtime.opik_enabled,
            opik_project_name=self._runtime.opik_project_name,
        )
        intent = await IntentParser(auxiliary_runtime, self._spec.model).parse(message)
        goal = await GoalBuilder(auxiliary_runtime, self._spec.model).build(intent)
        await FeasibilityChecker(self._runtime.tool_registry).check(goal)
        return await self.run(goal)

    def _resolve_model(self) -> None:
        if self._runtime.transport_registry is None:
            raise AgentError("No transport configured. Set AgentRuntime.transport_registry.")
        self._model = self._spec.model.resolve(self._runtime.transport_registry)

    async def run(
        self,
        goal: Goal,
        *,
        _resume_turn_index: int = 0,
        _resume_started_at: str | None = None,
        _resume_todos: list[Todo] | None = None,
        _resume_extensions: dict[str, Any] | None = None,
        _skip_start: bool = False,
    ) -> AgentResult:
        if self._model is None:
            self._resolve_model()
        if self._context is None:
            from app.agent_loop_lib.context.manager import ContextManager
            self._context = ContextManager()

        self._scope = RunScope(
            identity=self._run_ctx, spec=self._spec, runtime=self._runtime, goal=goal,
            session_id=self._session_id, resume_turn_index=_resume_turn_index,
            todos=list(_resume_todos) if _resume_todos is not None else [],
        )
        # One-shot: a parent scope stashed by `run_child()` is consumed on
        # the FIRST `run()` call only (re-running the same Agent instance,
        # e.g. across a resume, must not re-inherit).
        if self._parent_scope is not None:
            self._scope._inherit_from(self._parent_scope)
            self._parent_scope = None
        if _resume_extensions:
            from app.agent_loop_lib.core.scope import known_persisted_slots
            self._scope.restore_extensions(_resume_extensions, known_persisted_slots())

        # Opik trace for this run (see transport/opik_tracing.py) — a no-op
        # context manager unless tracing is enabled AND this is a ROOT run
        # (no parent_run_id); every LLM-call span made anywhere during the
        # `with` block, including by sub-agents awaited inline below, nests
        # under this one trace automatically via Opik's own context storage.
        from app.agent_loop_lib.transport.opik_tracing import maybe_start_run_trace

        with maybe_start_run_trace(
            enabled=self._runtime.opik_enabled, run_ctx=self._run_ctx,
            goal=goal, project_name=self._runtime.opik_project_name,
        ) as trace:
            if _skip_start:
                self._scope.started_at = _resume_started_at or _now()
            else:
                await self.emit(EventType.AGENT_START, {"goal": goal.description})
                self._scope.started_at = _now()
                await obs.write_state(self, goal, "starting", turn_index=0, started_at=self.started_at)
                await obs.append_timeline(self, "agent_start", f"Agent started: {goal.description[:80]}", "starting", {"goal": goal.description})

                try:
                    await hooks.dispatch_pre_agent(self._hooks, goal, scope=self._scope)
                except HookBlocked as e:
                    result = AgentResult(goal=goal, turns=[], success=False, error=f"Blocked before start: {e}")
                    await obs.write_state(self, goal, "failed", turn_index=0, started_at=self.started_at)
                    await obs.append_timeline(self, "agent_blocked", f"pre_agent hook blocked run: {e}", "failed", {"error": str(e)})
                    await self._post_agent(result)
                    return result

                await self._context.add(UserMessage(content=goal.description))

            result = await self._spec.loop.run(self, goal)
            try:
                trace.output = {
                    "success": result.success,
                    "output": str(result.output) if result.output is not None else None,
                    "error": result.error,
                    "turns": len(result.turns),
                }
            except Exception:
                pass
            return result

    # ---- the step primitive ----

    async def step(self, goal: Goal, turn_index: int) -> StepOutcome:
        """One turn: PRE_TURN guards -> context shaping -> guarded model
        call -> tool dispatch -> POST_TURN. The one fixed unit every
        `LoopStrategy` calls, any number of times, in any order. Hooks
        dispatched here apply identically no matter which loop is driving —
        this is what keeps deterministic control (budget, cancellation,
        guardrails, truncation recovery) middleware-owned regardless of
        loop shape.
        """
        spec, runtime, context = self._spec, self._runtime, self._context

        # Per-turn ambient state (see `core/scope.py`) — created once here
        # and threaded through every hook dispatch and tool call this turn
        # makes, so middleware can reach `ctx.scope.turn.run...` uniformly
        # regardless of which event fired.
        turn_scope = TurnScope(run=self._scope, turn_index=turn_index)

        try:
            await hooks.dispatch_pre_turn(self._hooks, turn_index, scope=turn_scope)
        except RunCancelled as e:
            return StepOutcome("stop", result=await self.fail(
                goal, str(e), event="agent_cancelled", summary="Agent cancelled", status="cancelled",
                detail={"turn_index": turn_index},
            ))
        except HookBlocked as e:
            return StepOutcome("stop", result=await self.fail(
                goal, f"Blocked before turn {turn_index}: {e}", event="turn_blocked",
                summary=f"pre_turn hook blocked turn {turn_index}: {e}",
            ))

        await self.emit(EventType.TURN_START, {"turn_index": turn_index})
        await obs.write_state(self, goal, "calling_llm", turn_index=turn_index, started_at=self.started_at)
        await obs.append_timeline(self, "llm_call", f"Calling LLM (turn {turn_index})", "calling_llm", {"turn_index": turn_index})

        messages = await context.messages()

        # --- Context shaping (pre_model hooks): the Phase-1 context-
        # engineering pipeline (budget reduction -> tool-result clearing ->
        # offload -> sliding window -> auto-compact) over what's about to be
        # sent, plus the deterministic deadline-warning guard. Stored
        # history in `context` is untouched — shaping is per-call.
        budget = ContextBudget.for_model(spec.model.model)
        messages = await hooks.dispatch_pre_model(
            self._hooks, messages, budget, turn_index=turn_index, max_turns=spec.max_turns, scope=turn_scope,
        )

        tool_schemas = tool_schemas_for_turn(self, spec, runtime)
        system_prompt = build_system_prompt(spec, runtime, goal, self._scope.todos, self._scope.extra_prompt_sections)

        llm_kwargs: dict = {}
        if spec.model.thinking_budget is not None:
            llm_kwargs["thinking_budget"] = spec.model.thinking_budget
        if spec.model.effort is not None:
            llm_kwargs["effort"] = spec.model.effort

        async def _call_llm() -> "ModelResponse":
            if not self._streaming:
                return await self._model.complete(
                    messages=messages, tools=tool_schemas or None, system=system_prompt,
                    model=spec.model.model, **llm_kwargs,
                )

            # --- Streaming branch: consume the StreamEvent stream, firing
            # per-token AG-UI events for text deltas, terminating on exactly
            # one StreamCompleteEvent carrying the full ModelResponse.
            await self.emit(EventType.TEXT_MESSAGE_START, {"turn_index": turn_index})
            final_response: "ModelResponse | None" = None
            # Reasoning brackets its own message, lazily opened on the
            # first `ThinkingDeltaEvent` (not every turn reasons) and
            # closed the moment text starts — reasoning always precedes
            # text within a turn (provider behavior) — or at turn end for
            # a reasoning-then-tool-call turn with no text at all.
            reasoning_open = False
            async for event in self._model.stream(
                messages=messages, tools=tool_schemas or None, system=system_prompt,
                model=spec.model.model, **llm_kwargs,
            ):
                if isinstance(event, ThinkingDeltaEvent):
                    if not reasoning_open:
                        await self.emit(EventType.REASONING_MESSAGE_START, {"turn_index": turn_index})
                        reasoning_open = True
                    await self.emit(EventType.REASONING_MESSAGE_CONTENT, {"delta": event.delta})
                elif isinstance(event, TextDeltaEvent):
                    if reasoning_open:
                        await self.emit(EventType.REASONING_MESSAGE_END, {"turn_index": turn_index})
                        reasoning_open = False
                    await self.emit(EventType.TEXT_MESSAGE_CONTENT, {"delta": event.delta})
                elif isinstance(event, StreamCompleteEvent):
                    final_response = event.response
            if reasoning_open:
                await self.emit(EventType.REASONING_MESSAGE_END, {"turn_index": turn_index})
            await self.emit(EventType.TEXT_MESSAGE_END, {"turn_index": turn_index})
            if final_response is None:
                raise AgentError("Model.stream() completed without a StreamCompleteEvent")
            return final_response

        # --- Guarded, hook-wrapped LLM call. Parallel guardrails: input
        # guardrails race the LLM call itself via asyncio.wait(FIRST_EXCEPTION)
        # instead of running serially before it.
        try:
            guardrail_task = asyncio.create_task(hooks.dispatch_guardrail_input(self._hooks, messages, scope=turn_scope))
            model_task = asyncio.create_task(hooks.call_model_wrapped(self._hooks, _call_llm))
            await asyncio.wait({guardrail_task, model_task}, return_when=asyncio.FIRST_EXCEPTION)

            if guardrail_task.done() and guardrail_task.exception() is not None:
                model_task.cancel()
                try:
                    await model_task
                except (Exception, asyncio.CancelledError):
                    pass
                raise guardrail_task.exception()

            if model_task.done() and model_task.exception() is not None:
                if not guardrail_task.done():
                    guardrail_task.cancel()
                try:
                    await guardrail_task
                except (Exception, asyncio.CancelledError):
                    pass
                raise model_task.exception()

            if not guardrail_task.done():
                await guardrail_task
            response = model_task.result()
        except HookBlocked as e:
            return StepOutcome("stop", result=await self.fail(
                goal, f"Guardrail blocked: {e}", event="guardrail_blocked",
                summary=f"Input guardrail blocked turn {turn_index}: {e}",
            ))
        except Exception as e:
            return StepOutcome("stop", result=await self.fail(
                goal, f"LLM call failed: {e}", event="llm_call_failed", summary=f"LLM call failed: {e}",
            ))

        response_msg = response.message
        self._usage.add(response.usage)
        if runtime.budget is not None:
            await runtime.budget.record_turn(
                response.usage.input_tokens, response.usage.output_tokens,
                response.usage.cache_read_tokens, response.usage.cache_write_tokens,
            )

        await context.add(response_msg)
        tool_calls = self._extract_tool_calls(response_msg)

        post_model_ctx = await hooks.dispatch_post_model(self._hooks, response_msg, tool_calls, turn_index, scope=turn_scope)
        if response_msg.truncated:
            await obs.append_timeline(
                self, "response_truncated",
                f"LLM response truncated at max output tokens (turn {turn_index})",
                "calling_llm", {"turn_index": turn_index, "had_tool_calls": bool(tool_calls)},
            )
            if tool_calls:
                trunc_results = post_model_ctx.recovery_tool_results or []
                for tr in trunc_results:
                    await context.add(ToolMessage(content=tr.content, tool_call_id=tr.tool_call_id))
                turn = AgentTurn(messages=[response_msg], tool_calls=tool_calls, tool_results=trunc_results)
            else:
                if post_model_ctx.recovery_message is not None:
                    await context.add(post_model_ctx.recovery_message)
                turn = AgentTurn(messages=[response_msg], tool_calls=[], tool_results=[])
            self._scope.turns.append(turn)
            await self.emit(EventType.TURN_COMPLETE, {"turn_index": turn_index})
            return StepOutcome("continue", turn=turn)

        if not tool_calls:
            # A POST_MODEL hook can veto "no tool calls -> done" by setting
            # `recovery_message` even on a non-truncated response — e.g. a
            # completion gate that requires an artifact-producing tool call
            # before accepting a text-only answer (see
            # `app/agents/agent_loop/hooks/completion_gate.py`), or a nudge
            # for an empty/whitespace-only response. Same shape as the
            # truncated branch above: inject the message and `continue`
            # instead of treating this as a successful terminal turn.
            if post_model_ctx.recovery_message is not None:
                await context.add(post_model_ctx.recovery_message)
                turn = AgentTurn(messages=[response_msg], tool_calls=[], tool_results=[])
                self._scope.turns.append(turn)
                await self.emit(EventType.TURN_COMPLETE, {"turn_index": turn_index})
                return StepOutcome("continue", turn=turn)

            output = self.extract_text(response_msg)
            try:
                await hooks.dispatch_guardrail_output(self._hooks, output or "", scope=turn_scope)
            except HookBlocked as e:
                return StepOutcome("stop", result=await self.fail(
                    goal, f"Output guardrail blocked: {e}", event="guardrail_blocked",
                    summary=f"Output guardrail blocked: {e}",
                ))
            # Deliberately NOT appended to `self._scope.turns`: a plain terminal
            # text response (no tool calls) has never counted as a "turn"
            # in `AgentResult.turns` — only turns that actually dispatched
            # tool calls do. `terminal_turn` exists solely so
            # `write_turn_memory` has something to record.
            terminal_turn = AgentTurn(messages=[response_msg], tool_calls=[], tool_results=[])
            result = await self.succeed(
                goal, output, [],
                event="agent_complete",
                summary=f"Agent completed after {turn_index + 1} turn(s)",
                detail={"output": output[:200] if output else ""},
            )
            await obs.write_turn_memory(self, terminal_turn, turn_index)
            return StepOutcome("stop", result=result)

        # --- Process tool calls ---
        turn = AgentTurn(messages=[response_msg], tool_calls=tool_calls)
        tool_results: list[ToolResult] = []
        task_done = False
        final_output: object = None
        turn_artifacts: list[Artifact] = []
        final_confidence: Confidence | None = None
        final_record_ids: list[str] = []
        final_needs_input: str | None = None

        seen_tool_calls = turn_scope.seen_tool_calls

        # Pre-launch every NON-detached spawn_agent call this turn (one or
        # many) as a concurrent, dependency-aware asyncio Task via
        # `spawn_scheduler` — a call with `depends_on` waits for its
        # prerequisite(s) and gets their result folded into its goal
        # before it actually spawns; calls with no dependency on each
        # other still run fully in parallel. See `agent/spawn_scheduler.py`
        # for why this replaced a plain "pre-launch every call, no
        # ordering" batch.
        #
        # `detach=true` calls are deliberately excluded here: they launch
        # their OWN child exactly once, from inside `SpawnAgentTool.
        # handle()` (see `tools/builtin/coordination/spawn_agent.py::
        # _run_detached_spawn`), tracked via `self._detached_tasks` instead
        # of `_pending_spawn_tasks`. Pre-launching them here too used to
        # cause a genuine double-spawn: this batch would launch a child via
        # `schedule_spawn_batch`, then `handle()` would launch a SECOND,
        # entirely separate child for the same call and never await (or
        # even reference) the first one — an orphaned task silently
        # mutating shared runtime state after the turn moved on, at 2x the
        # cost.
        spawn_calls = [
            c for c in tool_calls
            if TAG_SPAWN_BATCH in runtime.tool_registry.tags_for_name(c.name)
            and not c.arguments.get("detach")
        ]
        self._pending_spawn_tasks = {}
        if spawn_calls:
            from app.agent_loop_lib.agent.spawn_scheduler import schedule_spawn_batch
            self._pending_spawn_tasks = await schedule_spawn_batch(
                self, runtime, spawn_calls, self._scope,
                goal=goal, turn_index=turn_index, started_at=self.started_at,
            )

        # --- Parallel non-spawn tool execution: every non-spawn call this
        # turn is independent by construction and runs concurrently.
        pre_call_messages = await context.messages()

        async def _run_one_tool_call(call: ToolCall, *, is_duplicate: bool) -> ToolCallOutcome:
            if runtime.cancellation_token is not None and runtime.cancellation_token.is_cancelled:
                return ToolCallOutcome(result=ToolResult(
                    tool_call_id=call.id, name=call.name,
                    content="Cancelled before execution", is_error=True,
                ))
            return await execute_tool_call(
                self, call, spec, runtime, goal, pre_call_messages, turn_index,
                self.started_at, is_duplicate, response_msg, turn_scope,
            )

        # Everything from here through the spawn-await loop below can raise
        # (a parallel tool call, a checkpoint write, a POST_TURN hook) AFTER
        # `schedule_spawn_batch` above has already launched real
        # `asyncio.Task`s for this turn's spawn_agent calls. Those tasks run
        # independently of this method's control flow — an exception here
        # does not stop them. Without this `finally`, a failure anywhere in
        # this block would leave them running orphaned: still executing
        # child agents, still mutating shared runtime/hook/tool_state, long
        # after `Agent.step()` itself has failed out from under them. See
        # `spawn_scheduler.cancel_pending_spawn_tasks` — a no-op for tasks
        # this method already awaited to completion on the success path,
        # so running it unconditionally is safe.
        try:
            outcomes_by_id: dict[str, ToolCallOutcome] = {}
            # Every TAG_SPAWN_BATCH call (detached or not) is handled below,
            # sequentially, never in this gather — a non-detached one is
            # just awaiting its already-running `_pending_spawn_tasks`
            # slot, and a detached one only needs to fire its background
            # task, so serializing them here costs nothing and keeps the
            # two spawn code paths (pre-launched batch vs. detach)
            # entirely out of the plain-tool concurrency below.
            parallel_calls = [
                c for c in tool_calls
                if TAG_SPAWN_BATCH not in runtime.tool_registry.tags_for_name(c.name)
            ]
            # Computed synchronously, in full, BEFORE any call in this wave
            # starts its own coroutine — see `compute_duplicate_flags()` for
            # why the check-then-add can't safely happen inside each call's
            # own (interleavable) coroutine.
            duplicate_flags = compute_duplicate_flags(parallel_calls, seen_tool_calls, runtime.tool_registry)
            if parallel_calls:
                results = await asyncio.gather(
                    *(_run_one_tool_call(c, is_duplicate=duplicate_flags[c.id]) for c in parallel_calls)
                )
                outcomes_by_id.update(zip((c.id for c in parallel_calls), results))
            for c in tool_calls:
                if TAG_SPAWN_BATCH in runtime.tool_registry.tags_for_name(c.name):
                    outcomes_by_id[c.id] = await _run_one_tool_call(c, is_duplicate=False)

            for call in tool_calls:
                outcome = outcomes_by_id[call.id]
                tool_results.append(outcome.result)
                if outcome.task_done:
                    task_done = True
                    final_output = outcome.final_output
                    turn_artifacts.extend(outcome.artifacts)
                    final_confidence = outcome.confidence
                    final_record_ids = outcome.record_ids
                    final_needs_input = outcome.needs_input

            for tr in tool_results:
                content_str = json.dumps(tr.content) if not isinstance(tr.content, str) else tr.content
                await context.add(ToolMessage(
                    content=content_str,
                    tool_call_id=tr.tool_call_id,
                    is_error=tr.is_error,
                    artifact_meta=tr.artifact_meta,
                ))

            turn.tool_results = tool_results
            self._scope.turns.append(turn)

            await obs.save_checkpoint(self, "post_tool", goal, await context.messages(), turn_index)
            await hooks.dispatch_post_turn(self._hooks, turn_index, turn, scope=turn_scope)
            await obs.write_turn_memory(self, turn, turn_index)
            await self.emit(EventType.TURN_COMPLETE, {"turn_index": turn_index})
        finally:
            from app.agent_loop_lib.agent.spawn_scheduler import cancel_pending_spawn_tasks
            await cancel_pending_spawn_tasks(self._pending_spawn_tasks)

        if task_done:
            result = await self.succeed(
                goal, final_output, turn_artifacts,
                event="task_complete",
                summary="Task completed via task_complete tool",
                detail={"output": str(final_output)[:200]},
                confidence=final_confidence,
                record_ids=final_record_ids,
                needs_input=final_needs_input,
            )
            return StepOutcome("stop", result=result)

        return StepOutcome("continue", turn=turn)

    # ---- helpers ----

    def _extract_tool_calls(self, msg: Message) -> list[ToolCall]:
        """`ToolCall`s live on `AssistantMessage.tool_calls` directly now —
        no more scanning `content` blocks for a `TOOL_USE` type tag."""
        if isinstance(msg, AssistantMessage) and msg.tool_calls:
            return list(msg.tool_calls)
        return []

    def stream(self, goal: Goal, **run_kwargs):
        """Streaming turn loop: run this goal while yielding `AgentEvent`s
        as they occur instead of only returning the final `AgentResult`.
        The final `AgentResult` is available as `self.last_stream_result`
        once the generator is exhausted."""
        return agent_streaming.stream(self, goal, **run_kwargs)

    async def resume(self, checkpoint_id: str, hil_responses: dict[str, str] | None = None) -> "AgentResult":
        """Resume execution from a saved checkpoint. See `agent/resume.py`."""
        return await agent_resume.resume(self, checkpoint_id, hil_responses=hil_responses)

    async def resume_thread(self, thread_id: str, hil_responses: dict[str, str] | None = None) -> "AgentResult":
        """Resume the latest checkpoint for a thread/run identity. See `agent/resume.py`."""
        return await agent_resume.resume_thread(self, thread_id, hil_responses=hil_responses)

    async def rollback(
        self, thread_id: str, turn_index: int, hil_responses: dict[str, str] | None = None,
    ) -> "AgentResult":
        """Time-travel to a prior checkpoint, continuing as a new branch. See `agent/resume.py`."""
        return await agent_resume.rollback(self, thread_id, turn_index, hil_responses=hil_responses)


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()
