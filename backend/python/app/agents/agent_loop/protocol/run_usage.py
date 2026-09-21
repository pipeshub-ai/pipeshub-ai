"""Wire payload for the opt-in CUSTOM `run_usage` stream frame: token totals
for every LLM call one chat run made, so external clients (the FRAMES
benchmark harness) can report cost without a second tracing system.
Rides on the same opt-in as `retrieval_context` (`includeRetrievalContext`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from app.agent_loop_lib.core.responses import RunUsage

RUN_USAGE_EVENT_NAME = "run_usage"
RUN_USAGE_SCHEMA_VERSION = 1


class RunUsagePayload(BaseModel):
    """`inputTokens` includes cache reads; `outputTokens` includes reasoning."""

    model_config = ConfigDict(extra="forbid")

    schemaVersion: int = RUN_USAGE_SCHEMA_VERSION
    model: str | None = None
    llmCalls: int = 0
    inputTokens: int = 0
    outputTokens: int = 0
    cacheReadTokens: int = 0
    cacheWriteTokens: int = 0
    # Subset of the totals above spent on auto-compact summaries.
    auxiliaryLlmCalls: int = 0
    # One `[input, output, cacheRead]` triple per LLM call, in call order.
    calls: list[list[int]] = Field(default_factory=list)
    # Loop outcome signals that otherwise never reach the stream.
    turns: int | None = None
    maxTurns: int | None = None
    completionGateNudges: int = 0
    agentError: str | None = None

    @classmethod
    def from_usage(cls, loop: RunUsage, auxiliary: RunUsage, *, model: str | None) -> RunUsagePayload:
        return cls(
            model=model,
            llmCalls=loop.requests + auxiliary.requests,
            inputTokens=loop.input_tokens + auxiliary.input_tokens,
            outputTokens=loop.output_tokens + auxiliary.output_tokens,
            cacheReadTokens=loop.cache_read_tokens + auxiliary.cache_read_tokens,
            cacheWriteTokens=loop.cache_write_tokens + auxiliary.cache_write_tokens,
            auxiliaryLlmCalls=auxiliary.requests,
            calls=[
                [u.input_tokens, u.output_tokens, u.cache_read_tokens]
                for u in (*loop.per_request, *auxiliary.per_request)
            ],
        )

    def to_wire_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


async def emit_run_usage(
    context: "AgentContext",
    agent: Any,
    result: Any,
    *,
    model: str | None = None,
) -> None:
    """One `run_usage` frame per opted-in run, before the finalizer closes the
    stream. No-op otherwise: this is measurement surface, not product output.

    The loop-outcome fields come from the agent rather than the usage
    accumulators — whether a run stopped because it exhausted its turns or
    because it decided it was done is not visible in a token count.
    """
    if not context.include_retrieval_context or context.event_sink is None:
        return
    payload = RunUsagePayload.from_usage(
        agent.usage, context.auxiliary_usage, model=model,
    )
    spec = getattr(agent, "spec", None)
    payload.turns = getattr(result, "turns", None)
    payload.maxTurns = getattr(spec, "max_turns", None)
    payload.completionGateNudges = context.completion_gate_nudges
    payload.agentError = getattr(result, "error", None)
    for event in context.formatter.run_usage(context, payload=payload.to_wire_dict()):
        await context.event_sink.write(event)
