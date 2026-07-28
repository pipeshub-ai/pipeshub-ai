"""Well-known `Tag` values the turn loop, `AgentRuntime`, and builtin
middleware key off of, instead of hardcoded `if call.name == "..."` string
comparisons scattered across `agent/__init__.py`, `runtime.py`, `tool_loop.
py`, and `hooks/middleware/builtin/supervisor_gate.py`.

Each constant here names a CAPABILITY, not a specific tool — "this call
should be pre-launched as an independent spawn task", "this call, if it
succeeds, ends the run", "an identical repeat of this call within one turn
is wasted work", "this call produced a plan a confidence gate should
evaluate". Any `Tool` (builtin or a host application's own) opts into that
capability by including the matching tag in its `tags` property; dispatch
sites then ask "does this tool have tag X", never "is this tool literally
named Y". Adding a new spawn-like or terminal-like tool anywhere in the
codebase needs no edit to `agent/__init__.py` or `tool_loop.py` — the
existing dispatch already recognizes the tag.

Declaring the tag value here rather than as a bare `Tag("spawn", "true")`
literal at each call site is what makes this enforceable in practice: grep
for a `TAG_*` name to find every consumer of a given capability, and a typo
in a tag's key/value is a single point of failure to fix rather than N
independently-drifting literals.
"""

from __future__ import annotations

from app.agent_loop_lib.tools.base import Tag

__all__ = [
    "TAG_DEDUP_EXACT",
    "TAG_LIFECYCLE_TERMINAL",
    "TAG_PLANNING_CREATE_PLAN",
    "TAG_SPAWN",
    "TAG_SPAWN_BATCH",
    "TAG_UI_ONLY",
]

# A tool call that launches one or more child agent runs through
# `AgentRuntime.run_child()` — `run_child()`'s own spawn-depth guard strips
# every tool_names entry carrying this tag once a child is one hop from
# `MAX_SPAWN_DEPTH`, instead of hardcoding the literal names "spawn_agent"
# and "best_of_n". Both `SpawnAgentTool` and `BestOfNTool` carry it; a
# future spawn-like tool needs no edit to the guard itself.
TAG_SPAWN = Tag("spawn", "true")

# Narrower than `TAG_SPAWN`: a call that must be pre-launched as an
# independent `asyncio.Task` via `agent/spawn_scheduler.py::
# schedule_spawn_batch` BEFORE the turn's non-spawn tool calls run, and
# awaited separately afterward — see `Agent.step()`'s `spawn_calls`/
# `parallel_calls` partitioning. Only `SpawnAgentTool` carries this one;
# `BestOfNTool` schedules its own candidates internally via
# `agent/best_of_n.py::run_best_of_n` and must NOT be pulled into that
# batch, so it gets `TAG_SPAWN` without `TAG_SPAWN_BATCH`.
TAG_SPAWN_BATCH = Tag("spawn", "batch")

# A tool call whose SUCCESSFUL result ends the current agent run — the turn
# loop calls the resolved tool's own `extract_outcome()` (see `tools.base.
# TerminalTool`) to get the stop signal and final output, instead of
# hardcoding `TaskCompleteTool.extract_outcome` by name.
TAG_LIFECYCLE_TERMINAL = Tag("lifecycle", "terminal")

# An identical repeat of this call (same name + same arguments) within one
# turn is redundant, side-effect-free work worth short-circuiting — see
# `agent/tool_loop.py::compute_duplicate_flags`.
TAG_DEDUP_EXACT = Tag("dedup", "exact")

# A tool call that produces a `Plan` (structured or free-form) whose
# confidence rating `supervisor_confidence_gate` should evaluate — see
# `hooks/middleware/builtin/supervisor_gate.py`.
TAG_PLANNING_CREATE_PLAN = Tag("planning", "create_plan")

# A tool that talks directly to the top-level HUMAN user through a UI
# surface the model doesn't otherwise render (e.g. a tappable-option
# question card) rather than returning data for the calling agent to
# reason over. Meaningful only for the agent the human is actually
# watching — `AgentRuntime.run_child()` strips every `TAG_UI_ONLY` tool
# from a spawned child's grant unconditionally (regardless of spawn
# depth), the same way it strips `TAG_SPAWN` tools one hop from
# `MAX_SPAWN_DEPTH`: a child's "question" has nowhere to go (there is no
# UI surface watching its own turns) and no way back into the parent's
# turn loop, so granting it one just gives the model a way to stall a
# whole spawn tree waiting on an answer nobody will ever provide.
TAG_UI_ONLY = Tag("interaction", "ui_only")
