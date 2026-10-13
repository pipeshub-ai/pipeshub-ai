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

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from app.agent_loop_lib.agent import Agent
    from app.agent_loop_lib.core.responses import RunUsage
    from app.agent_loop_lib.core.types import AgentResult
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

RUN_USAGE_EVENT_NAME = "run_usage"
# 3: `calls` covers sub-agent and grep-generation calls too, labelled by `callSources`.
RUN_USAGE_SCHEMA_VERSION = 3
_MAX_AGENT_ERROR_CHARS = 500
# The totals always cover every call; only the per-call breakdown is cut.
MAX_CALLS_PER_FRAME = 200

MAIN_LOOP_SOURCE = "main"
AUTO_COMPACT_SOURCE = "auto_compact"
GREP_GENERATION_SOURCE = "grep_generation"


@dataclass
class UsageLedger:
    """Every LLM call one request made outside its main agent loop, labelled
    by `source`: `auto_compact`, `grep_generation`, `sub_agent:<name>`.
    Only wired when the request opted in, so a normal request records nothing."""

    entries: list[tuple[str, TokenUsage]] = field(default_factory=list)

    def add(self, source: str, usage: TokenUsage) -> None:
        self.entries.append((source, usage))


def grep_generation_usage_reporter(
    state: Mapping[str, Any] | None,
) -> Callable[[BaseMessage], None] | None:
    """The `on_llm_usage` callback for grep-command generation: reports into
    the request's `UsageLedger`, or None when the request did not opt in."""
    ledger = state.get("usage_ledger") if state else None
    if not isinstance(ledger, UsageLedger):
        return None

    def _report(message: BaseMessage) -> None:
        from app.agents.agent_loop.converters import token_usage_from_ai_message

        if isinstance(message, AIMessage):
            ledger.add(GREP_GENERATION_SOURCE, token_usage_from_ai_message(message))

    return _report


class RunUsagePayload(BaseModel):
    """`inputTokens` includes cache reads and writes; `outputTokens` includes reasoning.
    Totals and `calls` cover the main loop and every other call the request
    made (sub-agents, compaction summaries, grep-command generation)."""

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
    # Calls outside the main loop, a subset of the totals above.
    auxiliaryLlmCalls: int = 0
    # One `[input, output, cacheRead, cacheWrite]` per LLM call: the main
    # loop's in call order, then the rest in the order they finished.
    calls: list[list[int]] = Field(default_factory=list)
    # The source of each entry of `calls`, same order.
    callSources: list[str] = Field(default_factory=list)
    # Loop outcome signals of the main loop only. `turns` counts the rounds
    # that continued the loop (tool calls, nudges); the final reply ends the
    # loop and is not one of them.
    turns: int | None = None
    maxTurns: int | None = None
    completionGateNudges: int = 0
    agentError: str | None = None
    # Set when `calls` lists only the first `MAX_CALLS_PER_FRAME` calls;
    # absent otherwise.
    truncated: bool | None = None

    @classmethod
    def from_usage(
        cls, loop: RunUsage, ledger: UsageLedger, *, model: str | None,
    ) -> RunUsagePayload:
        labelled = [
            *((MAIN_LOOP_SOURCE, usage) for usage in loop.per_request),
            *ledger.entries,
        ]
        shown = labelled[:MAX_CALLS_PER_FRAME]
        return cls(
            model=model,
            llmCalls=len(labelled),
            inputTokens=sum(u.input_tokens for _, u in labelled),
            outputTokens=sum(u.output_tokens for _, u in labelled),
            cacheReadTokens=sum(u.cache_read_tokens for _, u in labelled),
            cacheWriteTokens=sum(u.cache_write_tokens for _, u in labelled),
            auxiliaryLlmCalls=len(ledger.entries),
            calls=[
                [u.input_tokens, u.output_tokens, u.cache_read_tokens, u.cache_write_tokens]
                for _, u in shown
            ],
            callSources=[source for source, _ in shown],
            truncated=len(labelled) > MAX_CALLS_PER_FRAME or None,
        )

    def to_wire_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


async def emit_run_usage(
    context: "AgentContext",
    agent: "Agent",
    result: "AgentResult",
    *,
    model: str | None = None,
) -> None:
    """One `run_usage` frame per opted-in run, before the finalizer closes the
    stream. No-op otherwise: this is measurement surface, not product output.

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
    try:
        payload = RunUsagePayload.from_usage(
            agent.usage, context.usage_ledger, model=model,
        )
        spec = getattr(agent, "spec", None)
        # `AgentResult.turns` is the list of recorded turns, not a count.
        turns = getattr(result, "turns", None)
        payload.turns = len(turns) if isinstance(turns, list) else None
        payload.maxTurns = getattr(spec, "max_turns", None)
        payload.completionGateNudges = context.completion_gate_nudges
        error = getattr(result, "error", None)
        payload.agentError = str(error)[:_MAX_AGENT_ERROR_CHARS] if error else None
        events = list(context.formatter.run_usage(context, payload=payload))
    except Exception:
        logger.exception("run_usage: dropping usage frame for run %s", getattr(context, "run_id", None))
        return
    for event in events:
        await context.event_sink.write(event)
