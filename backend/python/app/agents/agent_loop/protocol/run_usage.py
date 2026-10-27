"""Wire payload for the opt-in CUSTOM `run_usage` stream frame: token totals
for every LLM call one chat run made, so external clients (evaluation
tools) can report cost without a second tracing system.
Rides on the same opt-in as `retrieval_context` (`includeRetrievalContext`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage, BaseMessage
from pydantic import BaseModel, ConfigDict, Field

from app.agent_loop_lib.core.responses import (
    TokenUsage,  # noqa: TC001 - dataclass field type
)
from app.agent_loop_lib.core.usage_source import MAIN_SOURCE

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from app.agent_loop_lib.agent import Agent
    from app.agent_loop_lib.core.types import AgentResult
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

RUN_USAGE_EVENT_NAME = "run_usage"
# 3: every model call of the request, each labelled in `callSources`.
RUN_USAGE_SCHEMA_VERSION = 3
_MAX_AGENT_ERROR_CHARS = 500
# The totals always cover every call; only the per-call breakdown is cut.
MAX_CALLS_PER_FRAME = 200

GREP_GENERATION_SOURCE = "grep_generation"
ENTITY_LINKING_SOURCE = "entity_linking"


@dataclass
class UsageLedger:
    """Every model call one request made, in the order they finished, each
    labelled by its source: `main`, `sub_agent:<name>`, `single_shot:<name>`,
    `auto_compact`, `plan_critic`, `result_critic`, `planner`, `replanner`
    (from the request's transports, see `UsageReportingTransport`),
    `grep_generation` and `entity_linking`. Only wired when the request opted
    in, so a normal request records nothing."""

    entries: list[tuple[str, TokenUsage]] = field(default_factory=list)

    def add(self, source: str, usage: TokenUsage) -> None:
        self.entries.append((source, usage))


def llm_usage_reporter(
    state: Mapping[str, Any] | None, source: str,
) -> Callable[[BaseMessage], None] | None:
    """The `on_llm_usage` callback for a model call a tool makes itself:
    reports into the request's `UsageLedger` under `source`, or None when the
    request did not opt in."""
    ledger = state.get("usage_ledger") if state else None
    if not isinstance(ledger, UsageLedger):
        return None

    def _report(message: BaseMessage) -> None:
        from app.agents.agent_loop.converters import token_usage_from_ai_message

        if isinstance(message, AIMessage):
            ledger.add(source, token_usage_from_ai_message(message))

    return _report


def grep_generation_usage_reporter(
    state: Mapping[str, Any] | None,
) -> Callable[[BaseMessage], None] | None:
    """The `on_llm_usage` callback for grep-command generation."""
    return llm_usage_reporter(state, GREP_GENERATION_SOURCE)


class RunUsagePayload(BaseModel):
    """`inputTokens` includes cache reads and writes; `outputTokens` includes reasoning.
    Totals and `calls` cover every model call of the request's `UsageLedger`."""

    # `validate_assignment` is what makes the loop-outcome fields below safe to
    # set after construction: without it Pydantic accepts any value on
    # assignment, so `turns = result.turns` (a `list[AgentTurn]`, not a count)
    # serialized as `[]` and only failed in the consumer.
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    schemaVersion: int = RUN_USAGE_SCHEMA_VERSION
    model: str | None = None
    llmCalls: int = 0
    inputTokens: int = 0
    outputTokens: int = 0
    cacheReadTokens: int = 0
    cacheWriteTokens: int = 0
    # Calls not made by the main agent loop, a subset of the totals above.
    auxiliaryLlmCalls: int = 0
    # One `[input, output, cacheRead, cacheWrite]` per LLM call, in the order
    # the calls finished.
    calls: list[list[int]] = Field(default_factory=list)
    # The source of each entry of `calls`, same order.
    callSources: list[str] = Field(default_factory=list)
    # Loop outcome signals of the main loop only, absent when the turn ended
    # before the loop ran (a clarifying question, a failure). `turns` counts
    # the rounds that continued the loop (tool calls, nudges); the final reply
    # ends the loop and is not one of them.
    turns: int | None = None
    maxTurns: int | None = None
    completionGateNudges: int = 0
    agentError: str | None = None
    # Set when `calls` lists only the first `MAX_CALLS_PER_FRAME` calls;
    # absent otherwise.
    truncated: bool | None = None

    @classmethod
    def from_ledger(cls, ledger: UsageLedger, *, model: str | None) -> RunUsagePayload:
        entries = ledger.entries
        shown = entries[:MAX_CALLS_PER_FRAME]
        return cls(
            model=model,
            llmCalls=len(entries),
            inputTokens=sum(u.input_tokens for _, u in entries),
            outputTokens=sum(u.output_tokens for _, u in entries),
            cacheReadTokens=sum(u.cache_read_tokens for _, u in entries),
            cacheWriteTokens=sum(u.cache_write_tokens for _, u in entries),
            auxiliaryLlmCalls=sum(1 for source, _ in entries if source != MAIN_SOURCE),
            calls=[
                [u.input_tokens, u.output_tokens, u.cache_read_tokens, u.cache_write_tokens]
                for _, u in shown
            ],
            callSources=[source for source, _ in shown],
            truncated=len(entries) > MAX_CALLS_PER_FRAME or None,
        )

    def to_wire_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


async def emit_run_usage(
    context: "AgentContext",
    agent: "Agent | None" = None,
    result: "AgentResult | None" = None,
    *,
    model: str | None = None,
) -> None:
    """The request's one `run_usage` frame, sent on every way an opted-in turn
    ends: after the loop, after a clarifying question, after a failure. A
    second call for the same request sends nothing, so a failure after the
    frame went out does not send another. No-op unless the request opted in:
    this is measurement surface, not product output.

    The loop-outcome fields come from the agent rather than the usage
    accumulators — whether a run stopped because it exhausted its turns or
    because it decided it was done is not visible in a token count.

    Never raises: this runs immediately before `AnswerFinalizer`, so anything
    escaping here would cost the user their answer to save a measurement.
    `validate_assignment` (above) still makes a malformed field fail loudly in
    tests, which is where that belongs.
    """
    if not context.include_retrieval_context or context.event_sink is None:
        return
    if not context.claim_run_usage_frame():
        return
    try:
        payload = RunUsagePayload.from_ledger(context.usage_ledger, model=model)
        if agent is not None:
            payload.maxTurns = getattr(getattr(agent, "spec", None), "max_turns", None)
        if result is not None:
            # `AgentResult.turns` is the list of recorded turns, not a count.
            turns = getattr(result, "turns", None)
            payload.turns = len(turns) if isinstance(turns, list) else None
            error = getattr(result, "error", None)
            payload.agentError = str(error)[:_MAX_AGENT_ERROR_CHARS] if error else None
        payload.completionGateNudges = context.completion_gate_nudges
        for event in context.formatter.run_usage(context, payload=payload):
            await context.event_sink.write(event)
    except Exception:
        logger.exception("run_usage: dropping usage frame for run %s", getattr(context, "run_id", None))
