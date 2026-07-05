"""Context objects that flow through the middleware pipelines.

Middleware never assigns a decision directly (there is no public setter for
``decision``); it calls ``ctx.deny(reason)`` / ``ctx.ask(reason)`` /
``ctx.block(reason)``. These escalate-only methods enforce the severity
ordering defined in `agent_loop.hooks.middleware.decisions`, so a decision can only
get *more* restrictive as it passes through the stack, never less. This is
what prevents a later, more permissive middleware from silently overriding
an earlier deny.

Each `HookEvent` (see `agent_loop.hooks.events`) is paired with exactly one
of these context types:

    PRE_TOOL_USE                    -> ToolCallContext
    POST_TOOL_USE                   -> ToolResultContext
    PRE_AGENT / POST_AGENT          -> AgentLifecycleContext
    PRE_TURN / POST_TURN            -> TurnContext
    PRE_MODEL                       -> ModelCallContext   (pure reducer, no decision)
    GUARDRAIL_INPUT / _OUTPUT       -> GuardrailContext
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from app.agent_loop_lib.hooks.middleware.decisions import (
    POST_SEVERITY,
    PRE_SEVERITY,
    PostDecision,
    PreDecision,
)
from app.agent_loop_lib.tools.base import Tag, ToolOutput

if TYPE_CHECKING:
    from app.agent_loop_lib.context.base import ContextBudget
    from app.agent_loop_lib.core.scope import RunScope, ToolScope, TurnScope
    from app.agent_loop_lib.core.types import (
        AgentResult,
        AgentTurn,
        Goal,
        Message,
        ToolCall,
    )
    from app.agent_loop_lib.core.types import ToolResult as CoreToolResult

__all__ = [
    "ToolCallContext",
    "ToolResultContext",
    "AgentLifecycleContext",
    "TurnContext",
    "ModelCallContext",
    "ModelResponseContext",
    "GuardrailContext",
]


@dataclass
class ToolCallContext:
    """Flows through the PreToolUse pipeline for a single tool invocation.

    Attributes:
        tool_path: Full path of the tool being called, e.g.
            '/toolsets/jira/create_issue'.
        tool_input: The call arguments. Middleware MAY mutate this dict in
            place to transform input before execution (e.g. redaction,
            defaulting, sanitization) - the mutated dict is what ultimately
            reaches `Tool.execute`.
        tool_use_id: Correlates this call with its corresponding
            `ToolResultContext` in the PostToolUse pipeline.
        caller: Provenance of the call - "agent" (LLM-initiated tool call) or
            "workflow" (directly triggered by a workflow node). Middleware can
            branch on this, e.g. skip human approval for workflow-triggered
            calls that were already approved at workflow-design time.
        tags: The resolved tool's effective tags (its own tags merged with its
            owning toolset's tags), attached by `ToolExecutor` before
            dispatch. Lets middleware scope itself by category/provider/etc.
            via `agent_loop.hooks.middleware.routing.by_tag` / `by_tags` instead of
            by path segment, e.g. `by_tag("category", "write")` rather than a
            path convention like `/toolsets/*/write/*`.
        metadata: Free-form bag for middleware to pass information forward
            (e.g. a risk score) - carried into the PostToolUse context by the
            executor via the shared `tool_use_id`.
    """

    tool_path: str
    tool_input: dict[str, Any]
    tool_use_id: UUID = field(default_factory=uuid4)
    caller: str = "agent"
    session_id: str | None = None
    tags: tuple[Tag, ...] = ()
    scope: "ToolScope | None" = None
    metadata: dict[str, Any] = field(default_factory=dict)
    _decision: PreDecision = field(default=PreDecision.ALLOW, repr=True)
    decision_reason: str | None = None

    @property
    def decision(self) -> PreDecision:
        return self._decision

    def deny(self, reason: str) -> None:
        """Escalate the decision to DENY (terminal; short-circuits the pipeline)."""
        self._escalate(PreDecision.DENY, reason)

    def ask(self, reason: str) -> None:
        """Escalate the decision to ASK (human-in-the-loop approval required).

        No-op if the decision is already DENY, since DENY is more severe.
        """
        self._escalate(PreDecision.ASK, reason)

    def _escalate(self, new: PreDecision, reason: str) -> None:
        if PRE_SEVERITY[new] > PRE_SEVERITY[self._decision]:
            self._decision = new
            self.decision_reason = reason


@dataclass
class ToolResultContext:
    """Flows through the PostToolUse pipeline after a tool has executed.

    Attributes:
        tool_response: The result produced by `Tool.execute`. Middleware MAY
            replace/redact this (e.g. strip secrets from output) by assigning
            a new `ToolResult` to this field.
        tags: The resolved tool's effective tags, carried over from the
            corresponding `ToolCallContext` so Post-stage middleware can also
            scope itself by tag (e.g. run a formatter only for
            `by_tag("category", "write")` tools).
        metadata: Inherited from the corresponding `ToolCallContext` (same
            `tool_use_id`) so Pre-stage findings (risk score, approval
            rationale, etc.) are available to Post-stage middleware.
        scope: The `ToolScope` for this call, carried over from the
            corresponding `ToolCallContext` — same object, so
            `ctx.scope.turn.run` reaches the owning run's ambient state
            (identity, budget, extension slots).
    """

    tool_path: str
    tool_use_id: UUID
    tool_response: ToolOutput
    caller: str = "agent"
    session_id: str | None = None
    tags: tuple[Tag, ...] = ()
    scope: "ToolScope | None" = None
    metadata: dict[str, Any] = field(default_factory=dict)
    _decision: PostDecision = field(default=PostDecision.CONTINUE, repr=True)
    decision_reason: str | None = None

    @property
    def decision(self) -> PostDecision:
        return self._decision

    def block(self, reason: str) -> None:
        """Escalate the decision to BLOCK (suppresses the result)."""
        self._escalate(PostDecision.BLOCK, reason)

    def _escalate(self, new: PostDecision, reason: str) -> None:
        if POST_SEVERITY[new] > POST_SEVERITY[self._decision]:
            self._decision = new
            self.decision_reason = reason


@dataclass
class AgentLifecycleContext:
    """Flows through the PreAgent/PostAgent pipelines, once per `Agent.run()`.

    `goal` is populated for PRE_AGENT; `result` is populated for POST_AGENT.
    A PRE_AGENT `deny()` aborts the run before the first turn starts.
    """

    goal: "Goal | None" = None
    result: "AgentResult | None" = None
    session_id: str | None = None
    scope: "RunScope | None" = None
    metadata: dict[str, Any] = field(default_factory=dict)
    _decision: PreDecision = field(default=PreDecision.ALLOW, repr=True)
    decision_reason: str | None = None

    @property
    def decision(self) -> PreDecision:
        return self._decision

    def deny(self, reason: str) -> None:
        self._escalate(PreDecision.DENY, reason)

    def ask(self, reason: str) -> None:
        self._escalate(PreDecision.ASK, reason)

    def _escalate(self, new: PreDecision, reason: str) -> None:
        if PRE_SEVERITY[new] > PRE_SEVERITY[self._decision]:
            self._decision = new
            self.decision_reason = reason


@dataclass
class TurnContext:
    """Flows through the PreTurn/PostTurn pipelines, once per agent turn.

    `turn` (the completed `AgentTurn`) is populated for POST_TURN only.
    """

    turn_index: int
    turn: "AgentTurn | None" = None
    session_id: str | None = None
    scope: "TurnScope | None" = None
    metadata: dict[str, Any] = field(default_factory=dict)
    _decision: PreDecision = field(default=PreDecision.ALLOW, repr=True)
    decision_reason: str | None = None

    @property
    def decision(self) -> PreDecision:
        return self._decision

    def deny(self, reason: str) -> None:
        self._escalate(PreDecision.DENY, reason)

    def ask(self, reason: str) -> None:
        self._escalate(PreDecision.ASK, reason)

    def _escalate(self, new: PreDecision, reason: str) -> None:
        if PRE_SEVERITY[new] > PRE_SEVERITY[self._decision]:
            self._decision = new
            self.decision_reason = reason


@dataclass
class ModelCallContext:
    """Flows through the PreModel pipeline: a pure reducer, not a decision gate.

    Every registered middleware receives the current `messages`/`budget` and
    is expected to return the (possibly transformed) message list assigned
    back onto `ctx.messages`, then call `next()`. There is no `deny`/`ask`
    here — context shaping never blocks a turn, it only reshapes what the
    model sees. `Pipeline` is instantiated with `is_terminal=lambda ctx: False`
    for this event, so every registered shaper always runs, in order.
    """

    messages: "list[Message]"
    budget: "ContextBudget"
    session_id: str | None = None
    scope: "TurnScope | None" = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # Populated by Agent.run() so deterministic, turn-count-aware shapers
    # (e.g. hooks.builtin.turn_guards.warn_before_deadline) can react to how
    # many turns remain without the turn loop itself owning that logic
    # inline. Both default so existing `ModelCallContext(messages=..., budget=...)`
    # construction (see tests/hooks/_helpers.py) stays valid untouched.
    turn_index: int = 0
    max_turns: int | None = None


@dataclass
class ModelResponseContext:
    """Flows through the PostModel pipeline: observes the model's raw
    response right after the LLM call, before tool calls are executed.

    Registered middleware can populate `recovery_message`/
    `recovery_tool_results` to supply WHAT should be fed back to the model
    when `response.truncated` is set (see
    `hooks.builtin.truncation_recovery.default_truncation_recovery`) — the
    turn loop still owns WHETHER to short-circuit the rest of the turn on a
    truncated response; this pipeline only shapes the recovery content.
    """

    response: "Message"
    tool_calls: "list[ToolCall]"
    turn_index: int
    session_id: str | None = None
    scope: "TurnScope | None" = None
    metadata: dict[str, Any] = field(default_factory=dict)
    recovery_message: "Message | None" = None
    recovery_tool_results: "list[CoreToolResult] | None" = None


@dataclass
class GuardrailContext:
    """Flows through the GuardrailInput/GuardrailOutput pipelines.

    `messages` is populated for GUARDRAIL_INPUT (the pending user-turn
    messages); `output` is populated for GUARDRAIL_OUTPUT (the model's
    proposed final answer). A `block()` short-circuits the pipeline —
    the agent loop treats this the same as the old `HookBlocked` exception.
    """

    messages: "list[Message] | None" = None
    output: str | None = None
    session_id: str | None = None
    scope: "TurnScope | None" = None
    metadata: dict[str, Any] = field(default_factory=dict)
    _decision: PostDecision = field(default=PostDecision.CONTINUE, repr=True)
    decision_reason: str | None = None

    @property
    def decision(self) -> PostDecision:
        return self._decision

    def block(self, reason: str) -> None:
        self._escalate(PostDecision.BLOCK, reason)

    def _escalate(self, new: PostDecision, reason: str) -> None:
        if POST_SEVERITY[new] > POST_SEVERITY[self._decision]:
            self._decision = new
            self.decision_reason = reason
