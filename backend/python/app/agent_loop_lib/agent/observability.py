from __future__ import annotations

import datetime
import json

from app.agent_loop_lib.core.scope import StateSlot
from app.agent_loop_lib.core.types import (
    AgentTurn,
    Goal,
    Message,
    MessageRole,
    ToolCall,
    ToolResult,
)
from app.agent_loop_lib.events.base import EventType

"""Side-effect writers used by `Agent`: state snapshots, the timeline log,
checkpoints, and turn memory. Every function takes the owning `agent` as
its first argument (duck-typed: needs `run_ctx`, `spec`, `runtime`,
`started_at`, `emit()`, `scope`) rather than living as methods, so
`Agent.step()` and `tool_loop.py` can call them uniformly and this module
stays independently testable.

None of this is agent *behavior* — it's harness observability plumbing that
happens to need identity fields off the agent.
"""

# Not `persist=True`: the sequence counter is meaningless across a
# checkpoint/resume boundary (the timeline store keeps growing across
# resumes with its own monotonic ids); not `inherit=True`: each child agent
# in a spawn tree gets its own timeline sequence starting at 0, since
# `TimelineEntry.sequence_id` is scoped per `agent_id`/`run_id`, not tree-wide.
_TIMELINE_SEQ: StateSlot[int] = StateSlot(key="observability.timeline_seq", default_factory=lambda: 0)


async def write_state(
    agent,
    goal: "Goal",
    status: str,
    turn_index: int,
    started_at: str,
    current_tool: str | None = None,
) -> None:
    from app.agent_loop_lib.modules.stores.state.base import AgentState, AgentStatus
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    state = AgentState(
        run_id=agent.run_ctx.run_id,
        agent_id=agent.run_ctx.agent_id,
        trace_id=agent.run_ctx.trace_id,
        parent_run_id=agent.run_ctx.parent_run_id,
        role_name=agent.spec.name,
        status=AgentStatus(status),
        current_turn=turn_index,
        current_tool=current_tool,
        goal_description=goal.description,
        started_at=started_at,
        updated_at=now,
    )
    # AG-UI STATE_SNAPSHOT: emitted regardless of whether a state_store is
    # configured — event consumers shouldn't have to opt into durable state
    # tracking just to observe live state.
    await agent.emit(EventType.STATE_SNAPSHOT, state.model_dump(mode="json"))
    if agent.runtime.state_store is not None:
        await agent.runtime.state_store.set(state)


async def append_timeline(
    agent,
    event_type: str,
    summary: str,
    status: str,
    detail: dict | None = None,
) -> None:
    if agent.runtime.timeline_store is None:
        return
    from app.agent_loop_lib.modules.stores.state.base import AgentStatus
    from app.agent_loop_lib.modules.stores.timeline.base import TimelineEntry
    seq = agent.scope.get(_TIMELINE_SEQ) + 1
    agent.scope.set(_TIMELINE_SEQ, seq)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    entry = TimelineEntry(
        sequence_id=seq,
        trace_id=agent.run_ctx.trace_id,
        run_id=agent.run_ctx.run_id,
        agent_id=agent.run_ctx.agent_id,
        parent_run_id=agent.run_ctx.parent_run_id,
        timestamp=now,
        status=AgentStatus(status),
        event_type=event_type,
        summary=summary,
        detail=detail or {},
        role_name=agent.spec.name,
        model=agent.spec.model.model,
    )
    await agent.runtime.timeline_store.append(entry)


async def save_checkpoint(
    agent,
    kind: str,
    goal: "Goal",
    messages: list["Message"],
    turn_index: int,
    current_tool: str | None = None,
    hil_request_id: str | None = None,
    pending_tool_call_id: str | None = None,
) -> str | None:
    """Save a checkpoint of the given kind. Returns checkpoint_id or None."""
    if agent.runtime.checkpoint_store is None:
        return None
    from app.agent_loop_lib.modules.providers.budget.base import BudgetSnapshot
    from app.agent_loop_lib.modules.stores.checkpoint.base import (
        AgentCheckpoint,
        CheckpointKind,
    )
    snapshot = await agent.runtime.budget.snapshot() if agent.runtime.budget else BudgetSnapshot()
    cp = AgentCheckpoint(
        run_id=agent.run_ctx.run_id,
        agent_id=agent.run_ctx.agent_id,
        parent_run_id=agent.run_ctx.parent_run_id,
        trace_id=agent.run_ctx.trace_id,
        role_name=agent.spec.name,
        model=agent.spec.model.model,
        goal=goal,
        messages=messages,
        turn_index=turn_index,
        budget_snapshot=snapshot,
        kind=CheckpointKind(kind),
        session_id=agent.session_id,
        current_tool=current_tool,
        hil_request_id=hil_request_id,
        pending_tool_call_id=pending_tool_call_id,
        started_at=agent.started_at,
        system_prompt_override=None,
        spawn_depth=agent.run_ctx.spawn_depth,
        todos=list(agent.todos),
        extensions=agent.scope.snapshot_extensions() if agent.scope is not None else {},
    )
    checkpoint_id = await agent.runtime.checkpoint_store.save(cp)
    await agent.emit(EventType.CHECKPOINT_SAVED, {"kind": kind, "turn_index": turn_index})
    return checkpoint_id


async def write_turn_memory(agent, turn: "AgentTurn", turn_index: int) -> None:
    """Write a turn's content to the memory provider (if configured)."""
    if agent.runtime.memory is None:
        return
    from app.agent_loop_lib.modules.providers.memory.base import MemoryScope

    scope = MemoryScope(
        agent_id=agent.run_ctx.agent_id,
        session_id=agent.session_id,
        team_id=agent.run_ctx.team_id,
    )
    base_meta = {
        "run_id": agent.run_ctx.run_id,
        "turn_index": turn_index,
        "role_name": agent.spec.name,
    }

    assistant_text = ""
    for msg in turn.messages:
        if msg.role == MessageRole.ASSISTANT:
            text = agent.extract_text(msg)
            if text:
                assistant_text = text
    if assistant_text:
        await agent.runtime.memory.add(
            content=assistant_text,
            metadata={**base_meta, "type": "assistant_response"},
            scope=scope,
        )

    for tr in turn.tool_results:
        content_str = json.dumps(tr.content) if not isinstance(tr.content, str) else tr.content
        summary = f"Tool '{tr.name}' result: {content_str[:500]}"
        await agent.runtime.memory.add(
            content=summary,
            metadata={**base_meta, "type": "tool_result", "tool": tr.name, "is_error": tr.is_error},
            scope=scope,
        )


async def handle_tool_approval(
    agent,
    call: ToolCall,
    reason: str,
    goal: Goal,
    messages: list[Message],
    turn_index: int,
) -> bool:
    """The `on_ask` callback `agent/tool_loop.py` wires into `ToolExecutor.
    call_tool()` — the HIL checkpoint/suspend path for `PreDecision.ASK`,
    same shape as `handle_clarify` below but for a yes/no approval decision
    rather than an open question: submit a HIL request, checkpoint so the
    pause survives a process restart, then block until a human answers.

    Returns whether the call was approved. `False` (never executes the
    tool) whenever there's no `hil_store` configured — same "nothing to ask,
    so don't allow it" fallback `ToolExecutor.call_tool()` already used
    before this existed, just no longer hardcoded into the executor itself.
    """
    runtime = agent.runtime
    if runtime.hil_store is None:
        return False

    from app.agent_loop_lib.modules.stores.hil.base import HILRequest, HILRequestType

    hil_request = HILRequest(
        request_type=HILRequestType.TOOL_APPROVAL,
        run_id=agent.run_ctx.run_id,
        session_id=agent.session_id,
        question=f"Approve tool call to '{call.name}'?",
        context={"tool": call.name, "arguments": call.arguments, "reason": reason},
    )
    request_id = await runtime.hil_store.submit(hil_request)

    # Same rationale as `handle_clarify`'s checkpoint: carries the ORIGINAL
    # tool_use id alongside the HIL request id so a caller resuming from a
    # crash mid-wait can still build a valid tool_result message.
    await save_checkpoint(
        agent, "hil_pause", goal, messages, turn_index,
        current_tool=call.name,
        hil_request_id=request_id,
        pending_tool_call_id=call.id,
    )

    await agent.emit(EventType.TOOL_CALL, {
        "tool": call.name, "hil_request_id": request_id, "tool_call_id": call.id,
        "awaiting_approval": True, "reason": reason,
    })

    hil_response = await runtime.hil_store.wait_for_response(request_id)
    return hil_response.approved


async def handle_clarify(
    agent,
    call: ToolCall,
    goal: Goal,
    messages: list[Message],
    turn_index: int,
) -> ToolResult:
    """Intercept the clarify tool: submit HIL request, wait for answer."""
    runtime = agent.runtime
    question = call.arguments.get("question", "")
    context_note = call.arguments.get("context", "")

    if runtime.hil_store is None:
        return ToolResult(
            tool_call_id=call.id, name=call.name,
            content="HIL store not configured — cannot pause for clarification",
            is_error=True,
        )

    from app.agent_loop_lib.modules.stores.hil.base import HILRequest, HILRequestType

    hil_request = HILRequest(
        request_type=HILRequestType.CLARIFICATION,
        run_id=agent.run_ctx.run_id,
        session_id=agent.session_id,
        question=question,
        context={"note": context_note} if context_note else {},
    )
    request_id = await runtime.hil_store.submit(hil_request)

    # Save a HIL_PAUSE checkpoint so callers can resume later. Carries the
    # ORIGINAL tool_use id (call.id) alongside the HIL request id so resume()
    # can build a valid tool_result message.
    await save_checkpoint(
        agent, "hil_pause", goal, messages, turn_index,
        current_tool="clarify",
        hil_request_id=request_id,
        pending_tool_call_id=call.id,
    )

    await agent.emit(EventType.TOOL_CALL, {"tool": "clarify", "hil_request_id": request_id, "tool_call_id": call.id})

    # Block until a human (or test) responds
    hil_response = await runtime.hil_store.wait_for_response(request_id)
    answer = hil_response.answer or ("approved" if hil_response.approved else "denied")

    return ToolResult(
        tool_call_id=call.id, name=call.name,
        content={"answer": answer, "approved": hil_response.approved},
    )
