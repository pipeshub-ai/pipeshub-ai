from __future__ import annotations

import json
import uuid

from app.agent_loop_lib.core.exceptions import AgentError
from app.agent_loop_lib.core.types import AgentResult, ToolMessage

"""Durable resume / rollback. Every function takes the owning `agent` as
its first argument, same convention as `observability.py` — these mutate
the agent's own `_context`/`_run_ctx` and re-enter `agent.run()` (which
rebuilds `RunScope`, restoring todos and `persist=True` `StateSlot`
extensions from the checkpoint) to actually continue on the calling
instance (not a throwaway one), so run identity across a resume/rollback
is genuine, not simulated.
"""


async def resume(
    agent,
    checkpoint_id: str,
    hil_responses: dict[str, str] | None = None,
) -> "AgentResult":
    """Resume execution from a saved checkpoint.

    Continues the SAME run identity (run_id/agent_id/trace_id/spawn_depth)
    from the exact turn_index the checkpoint was saved at, with the budget
    counters restored from the checkpoint's snapshot.

    hil_responses: maps hil_request_id -> answer string. These are injected
    as TOOL messages before re-entering the turn loop, addressed to the
    ORIGINAL clarify tool_use id (not the internal hil_request_id), which
    is what providers actually require.
    """
    runtime = agent.runtime
    if runtime.checkpoint_store is None:
        raise AgentError("Cannot resume: no checkpoint_store configured")

    checkpoint = await runtime.checkpoint_store.load(checkpoint_id)

    from app.agent_loop_lib.context.manager import ContextManager
    context = ContextManager()
    for msg in checkpoint.messages:
        await context.add(msg)

    if hil_responses and checkpoint.hil_request_id:
        answer = hil_responses.get(checkpoint.hil_request_id, "")
        tool_call_id = checkpoint.pending_tool_call_id or checkpoint.hil_request_id
        hil_msg = ToolMessage(
            content=json.dumps({"approved": True, "answer": answer}),
            tool_call_id=tool_call_id,
        )
        await context.add(hil_msg)

    agent._context = context
    if runtime.budget is not None:
        await runtime.budget.restore(checkpoint.budget_snapshot)

    agent._run_ctx = agent._run_ctx.model_copy(update={
        "run_id": checkpoint.run_id,
        "agent_id": checkpoint.agent_id,
        "parent_run_id": checkpoint.parent_run_id,
        "trace_id": checkpoint.trace_id,
        "spawn_depth": checkpoint.spawn_depth,
    })

    # checkpoint.messages already contains this turn's assistant response
    # (the tool_use block that triggered the pause); once the HIL answer is
    # injected above, that turn is complete — continue at the NEXT turn
    # rather than re-issuing the LLM call for turn_index again.
    #
    # KNOWN LIMITATION: if the paused turn contained multiple tool calls
    # (e.g. clarify() alongside another tool), any calls after the one that
    # paused were never executed and their results are not part of this
    # checkpoint — only the single-clarify-per-turn case (the common one)
    # resumes cleanly. Also: resuming mid-way through a non-ReAct
    # `LoopStrategy` (e.g. `PlanCritiqueExecuteLoop`) restarts that
    # strategy's phase tracking from scratch even though the turn loop
    # itself continues from `checkpoint.turn_index + 1` — full resume of
    # arbitrary loop-strategy phase state needs the strategy to persist its
    # own phase marker, which is a natural extension point but not
    # implemented here.
    return await agent.run(
        checkpoint.goal,
        _resume_turn_index=checkpoint.turn_index + 1,
        _resume_started_at=checkpoint.started_at,
        _resume_todos=list(checkpoint.todos),
        _resume_extensions=dict(checkpoint.extensions),
        _skip_start=True,
    )


async def resume_thread(
    agent,
    thread_id: str,
    hil_responses: dict[str, str] | None = None,
) -> "AgentResult":
    """`Agent.resume(thread_id)` ergonomic: resume the LATEST checkpoint for
    a run/thread identity across a process restart, when the caller only
    knows "which conversation" (the stable `run_id`) rather than a specific
    `checkpoint_id`. `thread_id` is `run_id` — this harness doesn't need a
    separate identifier since run_id already survives pause/resume
    unchanged (see `resume()`).
    """
    runtime = agent.runtime
    if runtime.checkpoint_store is None:
        raise AgentError("Cannot resume: no checkpoint_store configured")
    checkpoint = await runtime.checkpoint_store.latest(thread_id)
    if checkpoint is None:
        raise AgentError(f"No checkpoint found for thread_id={thread_id!r}")
    return await resume(agent, checkpoint.checkpoint_id, hil_responses=hil_responses)


async def rollback(
    agent,
    thread_id: str,
    turn_index: int,
    hil_responses: dict[str, str] | None = None,
) -> "AgentResult":
    """Time-travel: restore conversation + state to the checkpoint at or
    before `turn_index` on `thread_id` and continue as a NEW branch.

    Session-tree semantics, not flat-log rewrite: the original thread's
    checkpoints are never mutated or deleted — this seeds a fresh
    checkpoint (new run_id, same agent_id/trace_id) carrying the
    rolled-back state plus `metadata["branched_from_*"]` linking it back to
    its origin, then resumes from THAT. The branch's run_id is
    `agent.run_ctx.run_id` after this returns, same as after run().
    """
    runtime = agent.runtime
    if runtime.checkpoint_store is None:
        raise AgentError("Cannot rollback: no checkpoint_store configured")
    history = await runtime.checkpoint_store.history(thread_id)
    candidates = [cp for cp in history if cp.turn_index <= turn_index]
    if not candidates:
        raise AgentError(
            f"No checkpoint at or before turn {turn_index} for thread_id={thread_id!r}"
        )
    source = max(enumerate(candidates), key=lambda pair: (pair[1].turn_index, pair[0]))[1]

    branch = source.model_copy(update={
        "checkpoint_id": str(uuid.uuid4()),
        "run_id": str(uuid.uuid4()),
        "metadata": {
            **source.metadata,
            "branched_from_run_id": thread_id,
            "branched_from_checkpoint_id": source.checkpoint_id,
            "branched_from_turn": source.turn_index,
        },
    })
    await runtime.checkpoint_store.save(branch)
    return await resume(agent, branch.checkpoint_id, hil_responses=hil_responses)
