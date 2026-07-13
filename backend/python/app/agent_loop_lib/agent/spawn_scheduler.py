"""Dependency-aware scheduling for a batch of `spawn_agent` tool calls made
in one turn.

Without this module, every `spawn_agent` call in a turn is launched as an
unconditionally concurrent `asyncio.Task` (see `Agent.step()`'s old
pre-launch block) with a completely isolated child context (`AgentRuntime.
run_child()` gives the child a fresh `ContextManager` and only its own
`goal` string — see `runtime/runtime.py`). That is correct for genuinely
independent workstreams, but wrong for a plan like "fetch Jira tickets,
then build a PDF from them": both children start at the same instant, so
the PDF child has no way to see the Jira child's output — it either races
ahead with no data, or the ordering is accidental. Relying on the LLM to
"just sequence dependent calls across turns" (the previous mitigation, see
`app/agents/agent_loop/loops/orchestrator.py`'s Phase 2 instructions) is
not enforcement; nothing failed loudly when the model got it wrong.

This module ports the ordering primitive the legacy LangGraph deep agent
already had right (`app/modules/agents/deep/sub_agent.py`'s event-based
`depends_on` resolution + `context_manager.py`'s dependency-result
injection) into a small, independently testable unit `Agent.step()` and
`SpawnAgentTool.handle()` both call through — the single scheduling
implementation, used identically whether a "batch" has one spawn call or
many:

  - every call in the batch launches immediately as its own `asyncio.Task`
  - a call with `depends_on` waits (via `asyncio.Event`) for each
    prerequisite task_id to finish, THEN receives that prerequisite's
    output appended to its own goal, THEN actually spawns its child
  - calls with no dependency on each other still run fully concurrently —
    no regression for genuinely parallel phases
  - an invalid batch (self-dependency, unknown task_id, duplicate task_id,
    or a dependency cycle) is rejected at validation time, per-call, with a
    corrective message the orchestrator model can act on next turn — never
    a crash, never a silent hang
  - a prerequisite that fails skips the dependent (rather than running it
    against missing data) and reports why

`SPAWN_RESULTS_SLOT` extends this across turns within one run: a
`depends_on` reference can name a task_id spawned (and already completed)
in an EARLIER turn, not just a sibling in the same batch — e.g. "spawn
jira in turn N, spawn pdf depends_on=[jira] in turn N+1" (which the
orchestrator's own Phase 2 loop can still end up doing if the model splits
dispatch across turns) resolves exactly the same way as both calls being
in the same turn.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.agent import observability as obs
from app.agent_loop_lib.core.scope import StateSlot
from app.agent_loop_lib.core.types import AgentResult, Goal, ToolCall

if TYPE_CHECKING:
    from app.agent_loop_lib.core.scope import RunScope
    from app.agent_loop_lib.runtime.runtime import AgentRuntime

__all__ = [
    "SPAWN_RESULTS_SLOT",
    "SpawnBatchPlan",
    "SpawnDependencyError",
    "schedule_spawn_batch",
    "validate_spawn_batch",
]

logger = logging.getLogger(__name__)

# Per-dependency cap on how much of a prerequisite's output gets inlined
# into a dependent's goal — generous enough for a data payload (a table of
# Jira tickets, a JSON blob) while keeping one bad dependency from blowing
# the dependent child's whole context budget.
_DEPENDENCY_RESULT_CHAR_CAP = 6000


class SpawnDependencyError(Exception):
    """Raised inside a scheduled spawn task for anything that keeps it
    from actually running a child: failed batch validation (unknown/
    duplicate/cyclic `depends_on`) or a prerequisite task that itself
    failed. Always raised from WITHIN the `asyncio.Task` `schedule_
    spawn_batch` hands back, so `await`ing that task surfaces it exactly
    like any other exception `SpawnAgentTool.handle()` already catches
    and turns into an error `ToolResult` — no new exception-handling path
    needed at the call site."""


@dataclass
class _CompletedSpawn:
    task_id: str
    result: AgentResult


# Keyed by task_id, one entry per `spawn_agent` call that has FINISHED
# (successfully or not) so far THIS RUN — including calls from earlier
# turns, which is what lets a `depends_on` reference reach across turns,
# not just siblings in the same batch. Lives on the PARENT's own `RunScope`
# (never `inherit=True`): a spawned child's own run starts with a fresh,
# empty registry, matching `run_child()`'s existing "child gets an
# isolated scope" contract — only the dispatching agent's own spawn
# batches feed this slot.
SPAWN_RESULTS_SLOT: StateSlot[dict[str, _CompletedSpawn]] = StateSlot(
    key="spawn_scheduler.completed_by_task_id", default_factory=dict,
)


@dataclass
class SpawnBatchPlan:
    """Validated shape of one `spawn_agent` batch — everything `schedule_
    spawn_batch` needs to actually launch it, computed once up front so
    the launch loop itself has no validation logic left to get wrong."""

    task_id_by_call_id: dict[str, str] = field(default_factory=dict)
    depends_by_call_id: dict[str, list[str]] = field(default_factory=dict)
    errors_by_call_id: dict[str, str] = field(default_factory=dict)


def _raw_task_id(call: ToolCall) -> str:
    explicit = call.arguments.get("task_id")
    return str(explicit).strip() if explicit else call.id


def _raw_depends_on(call: ToolCall) -> list[str]:
    raw = call.arguments.get("depends_on") or []
    if not isinstance(raw, list):
        return []
    return [str(dep).strip() for dep in raw if str(dep).strip()]


def _find_cycle(
    call_ids: list[str],
    depends_by_call_id: dict[str, list[str]],
    call_id_by_task_id: dict[str, str],
) -> list[str] | None:
    """DFS cycle detection over the in-batch dependency edges only
    (`depends_on` entries resolving to an ALREADY-COMPLETED task_id from an
    earlier turn can never participate in a cycle — that work is already
    done). Returns the call_ids forming one cycle, in order, or `None`."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {cid: WHITE for cid in call_ids}
    stack: list[str] = []

    def visit(cid: str) -> list[str] | None:
        color[cid] = GRAY
        stack.append(cid)
        for dep_task_id in depends_by_call_id.get(cid, []):
            dep_cid = call_id_by_task_id.get(dep_task_id)
            if dep_cid is None or dep_cid not in color:
                continue  # resolves to an already-completed task, not a batch sibling
            if color[dep_cid] == GRAY:
                idx = stack.index(dep_cid)
                return [*stack[idx:], dep_cid]
            if color[dep_cid] == WHITE:
                found = visit(dep_cid)
                if found is not None:
                    return found
        stack.pop()
        color[cid] = BLACK
        return None

    for cid in call_ids:
        if color[cid] == WHITE:
            found = visit(cid)
            if found is not None:
                return found
    return None


def _propagate_invalidity(
    calls: list[ToolCall],
    task_id_by_call_id: dict[str, str],
    depends_by_call_id: dict[str, list[str]],
    call_id_by_task_id: dict[str, str],
    errors: dict[str, str],
) -> None:
    """A call that depends on an already-invalid sibling can never
    actually run (its prerequisite's task_id will never be recorded into
    `SPAWN_RESULTS_SLOT`, so waiting on it would hang forever) — mark it
    invalid too, transitively, to a fixed point."""
    changed = True
    while changed:
        changed = False
        for call in calls:
            if call.id in errors:
                continue
            task_id = task_id_by_call_id[call.id]
            for dep in depends_by_call_id.get(call.id, []):
                dep_call_id = call_id_by_task_id.get(dep)
                if dep_call_id is not None and dep_call_id in errors:
                    errors[call.id] = (
                        f"spawn_agent task '{task_id}' depends on '{dep}', which is itself "
                        f"invalid: {errors[dep_call_id]}"
                    )
                    changed = True
                    break


def validate_spawn_batch(calls: list[ToolCall], known_task_ids: set[str]) -> SpawnBatchPlan:
    """Pure, synchronous validation of one `spawn_agent` batch — no I/O, no
    `asyncio`, safe to unit test directly. `known_task_ids` is every
    task_id already recorded in `SPAWN_RESULTS_SLOT` (i.e. completed in an
    earlier turn of this same run)."""
    task_id_by_call_id: dict[str, str] = {}
    depends_by_call_id: dict[str, list[str]] = {}
    errors: dict[str, str] = {}
    call_id_by_task_id: dict[str, str] = {}

    for call in calls:
        task_id = _raw_task_id(call)
        task_id_by_call_id[call.id] = task_id
        if task_id in known_task_ids or task_id in call_id_by_task_id:
            errors[call.id] = (
                f"task_id '{task_id}' is already used by a completed or sibling spawn_agent "
                "call this run — task_ids must be unique. Retry with a different task_id."
            )
        else:
            call_id_by_task_id[task_id] = call.id

    for call in calls:
        if call.id in errors:
            continue
        task_id = task_id_by_call_id[call.id]
        deps = _raw_depends_on(call)
        depends_by_call_id[call.id] = deps
        for dep in deps:
            if dep == task_id:
                errors[call.id] = f"spawn_agent task '{task_id}' cannot list itself in depends_on."
                break
            if dep not in known_task_ids and dep not in call_id_by_task_id:
                errors[call.id] = (
                    f"spawn_agent task '{task_id}' has depends_on=[{dep!r}], but {dep!r} is "
                    "not the task_id of any sibling spawn_agent call in this turn, nor a "
                    "previously completed spawn_agent task this run. Check the spelling, or "
                    "drop this dependency if the task is independent."
                )
                break

    _propagate_invalidity(calls, task_id_by_call_id, depends_by_call_id, call_id_by_task_id, errors)

    valid_call_ids = [c.id for c in calls if c.id not in errors]
    cycle = _find_cycle(valid_call_ids, depends_by_call_id, call_id_by_task_id)
    if cycle is not None:
        cycle_task_ids = [task_id_by_call_id[cid] for cid in cycle]
        message = "circular dependency detected among spawn_agent tasks: " + " -> ".join(cycle_task_ids)
        for cid in cycle:
            errors[cid] = message
        _propagate_invalidity(calls, task_id_by_call_id, depends_by_call_id, call_id_by_task_id, errors)

    return SpawnBatchPlan(
        task_id_by_call_id=task_id_by_call_id,
        depends_by_call_id=depends_by_call_id,
        errors_by_call_id=errors,
    )


def _format_dependency_section(task_id: str, result: AgentResult) -> str:
    output = result.output
    text = output if isinstance(output, str) else json.dumps(output, default=str)
    if len(text) > _DEPENDENCY_RESULT_CHAR_CAP:
        text = text[:_DEPENDENCY_RESULT_CHAR_CAP] + "\n... [truncated]"
    return f"### Result from prerequisite task '{task_id}'\n{text}"


def _augment_goal(goal: Goal, sections: list[str]) -> Goal:
    if not sections:
        return goal
    prefix = "## Results from prerequisite tasks\n\n" + "\n\n".join(sections) + "\n\n## Your task\n\n"
    return goal.model_copy(update={"description": prefix + goal.description})


async def _run_dependent_spawn(
    *,
    agent: Any,
    runtime: "AgentRuntime",
    call: ToolCall,
    task_id: str,
    deps: list[str],
    events: dict[str, asyncio.Event],
    run_scope: "RunScope",
    team_id: str,
) -> AgentResult:
    """One scheduled spawn's full lifecycle: wait on prerequisites (if
    any), fail fast if one of them failed, otherwise inline their output
    into the goal and launch the child — always signaling `events[task_id]`
    on the way out (success OR failure) so anything waiting on THIS task
    doesn't hang forever."""
    try:
        prereq_sections: list[str] = []
        for dep_id in deps:
            completed_by_id = run_scope.get(SPAWN_RESULTS_SLOT)
            completed = completed_by_id.get(dep_id)
            if completed is None:
                event = events.get(dep_id)
                if event is not None:
                    await event.wait()
                completed = run_scope.get(SPAWN_RESULTS_SLOT).get(dep_id)
            if completed is None:
                raise SpawnDependencyError(
                    f"prerequisite task '{dep_id}' for spawn_agent task '{task_id}' never "
                    "produced a result — cannot proceed."
                )
            if not completed.result.success:
                raise SpawnDependencyError(
                    f"prerequisite task '{dep_id}' failed "
                    f"({completed.result.error or 'unknown error'}) — task '{task_id}' was "
                    "not run. Fix or re-run the prerequisite before retrying this task."
                )
            prereq_sections.append(_format_dependency_section(dep_id, completed.result))

        from app.agent_loop_lib.tools.builtin.coordination.spawn_agent import (
            build_spawn_child,
        )
        child_spec, base_goal = build_spawn_child(runtime, call)
        goal = _augment_goal(base_goal, prereq_sections)

        result = await runtime.run_child(
            child_spec, goal, agent.run_ctx, team_id=team_id,
            session_id=agent.session_id, parent_scope=agent.scope,
        )
        run_scope.get(SPAWN_RESULTS_SLOT)[task_id] = _CompletedSpawn(task_id=task_id, result=result)
        return result
    finally:
        event = events.get(task_id)
        if event is not None:
            event.set()


async def _fail_immediately(message: str) -> AgentResult:
    raise SpawnDependencyError(message)


async def schedule_spawn_batch(
    agent: Any,
    runtime: "AgentRuntime",
    spawn_calls: list[ToolCall],
    run_scope: "RunScope",
    *,
    goal: Goal,
    turn_index: int,
    started_at: str | None,
) -> dict[str, asyncio.Task]:
    """Validate and launch one batch of `spawn_agent` calls (from a single
    turn — may be a batch of one). Returns a `{call.id: asyncio.Task}` map
    the caller `await`s per-call, same shape `Agent._pending_spawn_tasks`
    has always exposed to `SpawnAgentTool.handle()` — the ONLY integration
    point between this scheduler and everything downstream, so `handle()`
    needed no changes to its own await/except contract.

    Every call's task, valid or not, resolves to either an `AgentResult`
    (success) or raises `SpawnDependencyError` (validation failure or a
    failed prerequisite) — never any other exception shape — so `handle()`
    and `Agent.step()`'s own `except Exception` around the await stays a
    correct, complete catch.
    """
    known_task_ids = set(run_scope.get(SPAWN_RESULTS_SLOT).keys())
    plan = validate_spawn_batch(spawn_calls, known_task_ids)

    events: dict[str, asyncio.Event] = {
        plan.task_id_by_call_id[call.id]: asyncio.Event()
        for call in spawn_calls
        if call.id not in plan.errors_by_call_id
    }
    team_id = str(uuid.uuid4())
    is_batch = len(spawn_calls) > 1

    tasks: dict[str, asyncio.Task] = {}
    for call in spawn_calls:
        task_id = plan.task_id_by_call_id[call.id]
        error = plan.errors_by_call_id.get(call.id)
        await obs.write_state(
            agent, goal, "spawning_agent", turn_index=turn_index,
            started_at=started_at, current_tool="spawn_agent",
        )
        await obs.append_timeline(
            agent, "spawn_agent",
            f"Spawning child agent (task_id={task_id!r})" + (" [rejected]" if error else ""),
            "spawning_agent",
            {
                "args": {**call.arguments, "_parallel": is_batch},
                "team_id": team_id,
                "task_id": task_id,
                "depends_on": plan.depends_by_call_id.get(call.id, []),
                **({"rejected_reason": error} if error else {}),
            },
        )
        if error is not None:
            tasks[call.id] = asyncio.create_task(_fail_immediately(error))
            continue
        tasks[call.id] = asyncio.create_task(
            _run_dependent_spawn(
                agent=agent, runtime=runtime, call=call, task_id=task_id,
                deps=plan.depends_by_call_id.get(call.id, []),
                events=events, run_scope=run_scope, team_id=team_id,
            )
        )
    return tasks
