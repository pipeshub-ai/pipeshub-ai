from __future__ import annotations

import asyncio
import uuid

from app.agent_loop_lib.agent import observability as obs
from app.agent_loop_lib.core.types import (
    AgentResult,
    Goal,
    ToolCall,
    ToolResult,
    UserMessage,
)

"""Run-level best-of-N: the `best_of_n` special route in
`tools/builtin/coordination/best_of_n.py` dispatches here. Kept as its own
module, same convention as `observability.py`, so this logic stays
unit-testable without a full Agent/turn loop.

Candidates run through `AgentRuntime.run_child()` — the same primitive
`spawn_agent` and `agent_as_tool()` use — so the max-depth guard, spec
scoping, and cancellation-token propagation all apply unchanged. The only
new piece is the judge: one structured LLM call over the candidates' final
outputs.
"""

_JUDGE_SYSTEM = (
    "You are a strict judge comparing candidate solutions to the SAME task. "
    "Pick exactly one winner by index, judging only against the stated "
    "criteria and whether the candidate actually achieved the goal — ignore "
    "style or length."
)


def _judge_schema(n: int) -> dict:
    return {
        "type": "object",
        "properties": {
            "winner_index": {"type": "integer", "minimum": 0, "maximum": max(n - 1, 0)},
            "reason": {"type": "string"},
        },
        "required": ["winner_index", "reason"],
    }


async def _run_one_candidate(agent, candidate_spec, candidate_goal: Goal, team_id: str) -> AgentResult:
    try:
        return await agent.runtime.run_child(
            candidate_spec, candidate_goal, agent.run_ctx, team_id=team_id, session_id=agent.session_id,
            parent_scope=agent.scope,
        )
    except Exception as e:
        return AgentResult(goal=candidate_goal, success=False, error=str(e))


async def _judge(
    agent,
    candidate_goal_desc: str,
    criteria: str,
    successful: list[tuple[int, AgentResult]],
) -> tuple[int, str]:
    """Returns (winner_index_into_`successful`, reason). Never raises — any
    failure here is caught by the caller and falls back to the first
    successful candidate, since a broken judge should never fail an
    otherwise-successful best_of_n call."""
    if len(successful) == 1:
        return 0, "Only one successful candidate — judge skipped."

    model = None
    if agent.runtime.transport_registry is not None:
        try:
            model = agent.spec.model.resolve(agent.runtime.transport_registry)
        except Exception:
            model = None
    if model is None:
        return 0, "No model available for judging; defaulting to the first successful candidate."

    candidates_text = "\n\n".join(f"Candidate {i}:\n{c.output}" for i, (_, c) in enumerate(successful))
    response = await model.complete_structured(
        messages=[UserMessage(
            content=f"Goal: {candidate_goal_desc}\n\nCriteria: {criteria}\n\n{candidates_text}",
        )],
        system=_JUDGE_SYSTEM,
        output_schema=_judge_schema(len(successful)),
        model=agent.spec.model.model,
    )
    verdict = response.data
    local_index = int(verdict.get("winner_index", 0))
    local_index = max(0, min(local_index, len(successful) - 1))
    return local_index, str(verdict.get("reason", ""))


async def run_best_of_n(
    agent,
    call: ToolCall,
    goal: Goal,
    turn_index: int,
    started_at: str,
) -> ToolResult:
    args = call.arguments
    n = max(2, min(int(args.get("n", 2)), 5))
    criteria = args.get("criteria", "")
    candidate_goal_desc = args.get("goal", "")
    candidate_goal = Goal(description=candidate_goal_desc)

    role_name: str = args["role"]
    explicit_tools: list[str] | None = args.get("tools")
    model: str | None = args.get("model")
    overrides: dict = {}
    if explicit_tools is not None:
        overrides["tool_names"] = explicit_tools
    if model:
        overrides["model"] = model
    candidate_spec = agent.runtime.spec_for_role(role_name, **overrides)

    await obs.write_state(agent, goal, "spawning_agent", turn_index=turn_index, started_at=started_at, current_tool="best_of_n")
    await obs.append_timeline(
        agent, "best_of_n", f"Launching {n} candidate(s) for judged best-of-N", "spawning_agent",
        {"args": args, "n": n},
    )

    team_id = str(uuid.uuid4())
    candidates = await asyncio.gather(*(
        _run_one_candidate(agent, candidate_spec, candidate_goal, team_id) for _ in range(n)
    ))

    successful = [(i, c) for i, c in enumerate(candidates) if c.success]
    if not successful:
        await obs.append_timeline(
            agent, "best_of_n_failed", f"All {n} best_of_n candidates failed", "running_tool",
            {"errors": [c.error for c in candidates]},
        )
        return ToolResult(
            tool_call_id=call.id, name=call.name,
            content=f"All {n} candidates failed: {[c.error for c in candidates]}",
            is_error=True,
        )

    winner_index = successful[0][0]
    try:
        local_index, judge_reason = await _judge(agent, candidate_goal_desc, criteria, successful)
        winner_index = successful[local_index][0]
    except Exception as e:
        judge_reason = f"Judge call failed ({e}); defaulting to the first successful candidate."

    winner = candidates[winner_index]
    await obs.append_timeline(
        agent, "best_of_n_verdict", f"best_of_n picked candidate {winner_index} of {n}", "running_tool",
        {"winner_index": winner_index, "n": n, "reason": judge_reason},
    )

    return ToolResult(
        tool_call_id=call.id, name=call.name,
        content={
            "output": winner.output,
            "n": n,
            "winner_index": winner_index,
            "judge_reason": judge_reason,
        },
    )
