from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextvars import ContextVar

"""Model-proof file handoff into a coding sandbox's FIRST fresh instance.

`AgentTool.handle()` (see `coordination/agent_tool.py`) stages bytes here,
scoped around its single `await runtime.run_child(...)` call for a
`share_parent_results=True` child. `CodingSandboxTool.execute()` (see
`sandbox/coding_sandbox.py`) uploads whatever is staged into every FRESH
sandbox the child creates during that span (i.e. every `run_code`/
`install_packages` call made with no `sandbox_id`) — reused sandboxes
(an explicit `sandbox_id` passed back in) are left alone, since the files
are already there from the fresh creation.

A `ContextVar` (not a `RunScope` `StateSlot`) is deliberate: the staged
files need to be visible to code running several async-call layers below
`AgentTool.handle()` — inside the child's OWN `Agent.run()`/`Agent.step()`,
which has no reference back to the parent's `RunScope` — without
threading a new parameter through every one of those layers.
`contextvars` propagate correctly here because everything happens on the
same `asyncio.Task` tree spawned from the `await run_child(...)` call:
a plain `await` never creates a new task, and `asyncio.gather`/
`asyncio.create_task` (used by the child's own turn loop to parallelize
independent tool calls, and by the parent's spawn scheduler) copy the
CURRENT context at task-creation time, which is still within this
module's `with` block for the whole duration of `run_child(...)`.

Deliberately no "consume once" semantics: a mutation made by `.set()`
inside a `asyncio.gather`-spawned task is local to that task's copied
context and never propagates back to the context `stage_input_files`
established, so trying to clear the var after the first upload would not
reliably stop a LATER fresh sandbox (in a later turn, its own freshly
copied context) from seeing the files again. Re-uploading the same small
JSON file into every fresh sandbox the child happens to create is a
harmless, idempotent cost — nowhere near worth the complexity of getting
"consume once" correct across task boundaries.

`add_staged_skill_resources`/`peek_staged_skill_resources` is a SEPARATE,
sibling `ContextVar` rather than reusing `stage_input_files`'s `with`
block, because the lifetime requirement is different: a skill's bundled
scripts need to survive for the rest of the agent run (any later
`run_code` call, any number of turns later — the `load_skill` tool call
that stages them isn't nested inside a `run_child` span at all in the
common top-level-agent case), not just one parent->child handoff. It is
additive (`add_`, not `set_`/`stage_`) because loading a second skill
must not clobber a first skill's already-staged files, and because unlike
`stage_input_files` there is no enclosing `with` block whose exit would
be the natural place to clear it.
"""

__all__ = [
    "PARENT_RESULTS_INPUT_PATH",
    "stage_input_files",
    "set_staged_input_files_for_task",
    "peek_staged_input_files",
    "add_staged_skill_resources",
    "peek_staged_skill_resources",
]

# The sandbox-relative path a `share_parent_results=True` `AgentTool.handle()`
# stages the calling agent's tool results at (see that module) — the single
# source of truth so the coordination layer (`agent_tool.py`) and the tool
# layer (`coding_sandbox.py`, whose `run_code` description references this
# exact path) can never drift apart.
PARENT_RESULTS_INPUT_PATH = "input/parent_tool_results.json"

_staged_input_files: ContextVar[dict[str, bytes] | None] = ContextVar(
    "_staged_input_files", default=None,
)
_staged_skill_resources: ContextVar[dict[str, bytes] | None] = ContextVar(
    "_staged_skill_resources", default=None,
)


@contextlib.contextmanager
def stage_input_files(files: dict[str, bytes] | None) -> Iterator[None]:
    """Make `files` (sandbox-relative path -> content) visible to
    `peek_staged_input_files()` for the duration of this `with` block. A
    falsy `files` is a no-op — no token is set — so an always-on call
    site costs nothing when there is nothing to stage.

    Nested blocks MERGE with (never replace) whatever an enclosing block
    already staged — these are real, load-bearing nestings, not a
    theoretical case: `spawn_scheduler._run_dependent_spawn` stages a
    prerequisite's artifacts (`input/artifacts/<task_id>/...`) around the
    whole dependent child's run, and inside that span the child's wrapper
    may call a `share_parent_results` `AgentTool` whose `handle()` stages
    `input/parent_tool_results.json` around the grandchild. Replacing
    would make the outer artifact files — which the goal text explicitly
    tells the grandchild to read — silently vanish from its sandbox. On
    a path collision the inner (more specific) block wins."""
    if not files:
        yield
        return
    merged = {**(_staged_input_files.get() or {}), **files}
    token = _staged_input_files.set(merged)
    try:
        yield
    finally:
        _staged_input_files.reset(token)


def set_staged_input_files_for_task(files: dict[str, bytes] | None) -> None:
    """Bare `.set()` (no enclosing `with`, no reset) — for PRE_TOOL_USE
    middleware, where `stage_input_files()`'s `with` block is the WRONG
    lifetime: `ToolExecutor.call_tool()` runs the entire PRE_TOOL_USE
    pipeline to completion (middleware `next_fn()` only advances to the
    NEXT middleware, never into `tool.execute()`) and only THEN calls
    `tool.execute()` — by which point a `with stage_input_files(...):
    await next_fn()` block has already unwound and reset the var back to
    `None`. See `coding_sandbox_artifact_staging` (`sandbox_bridge.py`)
    for the call site this exists for.

    Safe specifically because PRE dispatch and `tool.execute()` for ONE
    tool call run sequentially on the SAME `asyncio.Task` (a plain
    `await`, never a new task in between) — so a set here is visible by
    the time `execute()` reads it via `peek_staged_input_files()` a few
    `await`s later, on that same task.

    Does not leak into a SIBLING or LATER tool call: every tool call —
    parallel or solo — is dispatched through the turn loop's
    `asyncio.gather` (see `agent/__init__.py`'s `_run_one_tool_call` /
    `parallel_calls`), which spawns one fresh `asyncio.Task` per call,
    each copying context from the turn's own ambient context at the
    `gather()` call site — never from a sibling task, and never from
    this function's mutation inside a PREVIOUS call's own task (a
    `ContextVar.set()` inside a spawned task is local to that task's
    copy and never propagates back up). So each tool call always starts
    from a clean baseline regardless of what an earlier or concurrent
    call staged.

    Merges with (never replaces) whatever an enclosing `stage_input_files`
    block already staged, same merge semantics as that function, so a
    PRE hook using this nested inside a `run_child`-staged span (parent
    -> child handoff) still sees the parent's files.

    A falsy `files` is a true no-op — the `ContextVar` is left completely
    untouched (not set to `{}`), so calling this unconditionally on every
    PRE_TOOL_USE dispatch (the call site's shape in `sandbox_bridge.py`,
    even when THIS call's `input_artifacts` was empty) never turns a
    clean `None` baseline into a spurious empty dict, and never disturbs
    an enclosing block's already-staged files when this call has nothing
    new to add."""
    if not files:
        return
    merged = {**(_staged_input_files.get() or {}), **files}
    _staged_input_files.set(merged)


def peek_staged_input_files() -> dict[str, bytes] | None:
    """Read-only lookup of whatever `stage_input_files()` currently has
    active in this context, or `None` if nothing is staged."""
    return _staged_input_files.get()


def add_staged_skill_resources(files: dict[str, bytes]) -> None:
    """Merge `files` (sandbox-relative path -> content, conventionally
    `skills/<skill-name>/<resource-path>`) into the set of bundled skill
    resources uploaded into every FRESH coding sandbox for the rest of
    this run — see `peek_staged_skill_resources` and the module docstring
    for why this isn't scoped to a `with` block. A falsy `files` is a
    no-op."""
    if not files:
        return
    current = dict(_staged_skill_resources.get() or {})
    current.update(files)
    _staged_skill_resources.set(current)


def peek_staged_skill_resources() -> dict[str, bytes] | None:
    """Read-only lookup of every skill resource staged so far in this
    context via `add_staged_skill_resources`, or `None` if none."""
    return _staged_skill_resources.get()
