from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING

from app.agent_loop_lib.core.exceptions import ToolError
from app.agent_loop_lib.core.types import Goal, ToolCall
from app.agent_loop_lib.core.types import ToolResult as CoreToolResult
from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.builtin.coordination.parent_results import (
    collect_parent_tool_results,
    extract_dependency_results,
    format_parent_results_digest,
    parent_results_as_json,
)
from app.agent_loop_lib.tools.builtin.sandbox.input_staging import (
    PARENT_RESULTS_INPUT_PATH,
    stage_input_files,
)

if TYPE_CHECKING:
    from app.agent_loop_lib.agent.spec import AgentSpec
    from app.agent_loop_lib.core.types import AgentResult
    from app.agent_loop_lib.runtime.runtime import AgentRuntime
    from app.agent_loop_lib.tools.special_route import RouteContext

__all__ = ["AgentTool", "MAX_AGENT_TOOL_DEPTH"]

"""Layer 5: composition — agents exposed as tools.

`AgentTool` is a REAL `Tool` subclass. Registering one on a `ToolRegistry`
under any name makes an entire `AgentSpec` callable exactly like any other
tool — PRE_TOOL_USE/POST_TOOL_USE middleware (permission, approval, budget,
audit-log) apply to the call uniformly, same as calling `web_search`. This
is how "any agent can be a tool for any other agent" composition works: a
top-level orchestrator's `tool_names` can simply include the names of other
`AgentTool`s (see `examples/02_orchestrator.py`).

Unlike `spawn_agent` (dynamic, model-chosen role by NAME), `AgentTool`
composition is STATIC — the developer wires the tool graph ahead of time.

Two dispatch paths, two recursion guards:

- Called from inside an agent run, `SpecialRouteRegistry` finds `handle()`
  and dispatches it with a `RouteContext` — the child then runs via
  `run_child(parent_run_ctx=...)`, inheriting the caller's trace identity,
  `session_id`, and `RunContext.spawn_depth` (so the framework's
  MAX_SPAWN_DEPTH guard applies uniformly to static composition and
  dynamic spawn_agent fan-out alike, and Opik spans nest under the
  caller's trace).
- `execute()` remains for direct invocation outside any agent run (a
  developer calling the tool as a plain function). There is no parent
  `RunContext` to inherit depth from on that path, so cycles between two
  `AgentTool`s are guarded by a plain `ContextVar` instead.
"""

MAX_AGENT_TOOL_DEPTH = 6

_agent_tool_depth: contextvars.ContextVar[int] = contextvars.ContextVar("_agent_tool_depth", default=0)


class AgentTool(Tool):
    """Wraps an `AgentSpec` as a plain, agent-callable `Tool`.

    `execute(goal, context=None)` runs the wrapped spec to completion via
    `runtime.run_child()` (the same primitive `spawn_agent`/`best_of_n`
    use) and returns its final output as an ordinary `ToolOutput` — the
    calling agent never sees a difference between this and a "real" tool.

    `share_parent_results=True` additionally hands the child the calling
    agent's own tool results from its CURRENT leg of work (see
    `coordination/parent_results.py`) — deterministically, not by hoping
    the calling model pastes data into the goal text. This matters
    because a statically composed child otherwise sees ONLY the goal
    string (`_goal_from_arguments` below): it cannot see the parent's
    conversation, so a parent that gathers data with its own tools (e.g.
    `jira_search_issues`) and then delegates "build a report from that"
    to a child with no way to reach Jira leaves the child with nothing to
    work with. With the flag on, `handle()` (the only dispatch path with
    access to the parent's message history) appends a truncated digest to
    the child's goal AND stages the full data as a file for the child's
    sandbox tools to pick up (see `sandbox/input_staging.py`) — covering
    both a model that reads its goal carefully and one that doesn't.
    `execute()` (no parent run in scope) cannot do this and is unaffected
    by the flag.

    `result_note`, when set, is appended verbatim to the child's SUCCESSFUL
    string output before it is returned to the calling agent. System-prompt
    instructions about how to treat a delegate's result carry little weight
    when the calling agent's context is large — instruction proximity wins.
    Placing the note inside the tool-result message itself puts it directly
    adjacent to the data it governs (e.g. "present these findings in full;
    do not summarize them"), which reliably survives a 100k+-token parent
    context where the same rule in the system prompt gets ignored. Error
    results are returned untouched — the note governs data presentation,
    not error handling.
    """

    def __init__(
        self,
        spec: "AgentSpec",
        runtime: "AgentRuntime",
        *,
        name: str | None = None,
        description: str | None = None,
        parameters: list[ToolParameter] | None = None,
        share_parent_results: bool = False,
        result_note: str | None = None,
    ) -> None:
        self._spec = spec
        self._runtime = runtime
        self._name = name or spec.name
        self._description = description or spec.description or f"Run the {self._name!r} agent on a goal."
        self._share_parent_results = share_parent_results
        self._result_note = result_note
        self._parameters = parameters or [
            ToolParameter(
                name="goal", type=ParameterType.STRING,
                description="The goal for this agent to accomplish.",
                required=True,
            ),
            ToolParameter(
                name="context", type=ParameterType.STRING,
                description="Optional extra context to append to the goal.",
                required=False, default=None,
            ),
        ]

    @property
    def name(self) -> str:
        return self._name

    @property
    def short_description(self) -> str:
        return self._description.splitlines()[0][:120]

    @property
    def description(self) -> str:
        return self._description

    @property
    def path(self) -> str:
        return f"/toolsets/agents/{self._name}"

    @property
    def parameters(self) -> list[ToolParameter]:
        return self._parameters

    @property
    def spec(self) -> "AgentSpec":
        """The wrapped `AgentSpec` — a read-through accessor so a caller
        that needs to run this agent WITHOUT going through `handle()`'s
        own dispatch (e.g. `spawn_agent`'s direct-dispatch optimization,
        see `tools/builtin/coordination/spawn_agent.py`) doesn't have to
        reach into `self._spec`."""
        return self._spec

    @property
    def share_parent_results(self) -> bool:
        """Whether this tool needs the CALLING agent's own conversation
        (`ctx.messages`) to build the child's goal — see the class
        docstring. A caller bypassing `handle()` (no `ctx.messages` of
        its own to offer) must check this and fall back to the normal
        `handle()` dispatch when `True`, or the child silently loses the
        parent-results digest/staged file it was configured to expect."""
        return self._share_parent_results

    def apply_result_note(self, output: object) -> object:
        """Public wrapper over `_with_result_note` — for a caller that
        runs this tool's `spec` directly via `runtime.run_child()` (see
        `share_parent_results` above) instead of through `handle()`, so
        `result_note` still gets applied either way."""
        return self._with_result_note(output)

    def finalize_output(self, result: "AgentResult") -> object:
        """Public wrapper over `_finalize_output` — for a caller that runs
        this tool's `spec` directly via `runtime.run_child()` (see
        `share_parent_results`/`apply_result_note` above) instead of
        through `handle()`/`execute()`, so a `needs_input` escalation
        still surfaces either way (see `spawn_agent.py::run_spawned_child`,
        which uses this instead of the narrower `apply_result_note` for
        exactly this reason)."""
        return self._finalize_output(result)

    def _finalize_output(self, result: "AgentResult") -> object:
        """The tool-result content for a SUCCESSFUL child run: `result_note`
        applied to `result.output`, plus an explicit `[ESCALATION]` suffix
        when the child set `needs_input` (the typed output contract, see
        `AgentResult` in `core/types.py`) — this composition path (a static
        domain `AgentTool`, not `spawn_agent`) returns a plain string/prose
        content, unlike `spawn_agent`'s dict-shaped result (see
        `tools/builtin/coordination/spawn_agent.py::child_result_content`),
        so the escalation is folded into that same string rather than a
        separate dict key nothing here would read."""
        output = self._with_result_note(result.output)
        if result.needs_input and isinstance(output, str):
            output = f"{output}\n\n[ESCALATION] {result.needs_input}"
        return output

    @staticmethod
    def _goal_from_arguments(arguments: dict) -> Goal:
        goal_text = arguments.get("goal", "")
        extra_context = arguments.get("context")
        description = f"{goal_text}\n\nContext: {extra_context}" if extra_context else goal_text
        return Goal(description=description)

    @staticmethod
    def _inherit_parent_skills(goal: Goal, ctx: "RouteContext") -> Goal:
        """Carries forward whatever skill body the CALLING agent already
        had injected into ITS OWN prompt — via an explicit `load_skill`
        call or the `skill_preloading` PRE_AGENT hook (see that
        middleware's docstring) — into the child's goal text.

        Necessary because `run_child()` always gives a statically-composed
        `AgentTool` child a brand-new `RunScope` ("its children always
        start with a clean scope" — see that method's docstring): a skill
        the parent already had in hand at the moment of delegation would
        otherwise never reach a child it hands the actual work to, even
        when the delegated task is exactly what that skill covers (e.g. a
        top-level agent loads the `pdf` skill, then delegates "generate
        the PDF" to `coding_agent`, which never sees it). The child still
        runs its OWN independent `skill_preloading` pass afterward, scored
        against ITS delegated goal rather than the parent's — this only
        backfills the specific skill(s) the parent already resolved,
        rather than making the child re-discover them from scratch.

        A no-op (returns `goal` unchanged) when the parent has nothing
        preloaded — the common case for a delegate whose own goal was
        never skill-relevant at the parent's level either."""
        preloaded = ctx.scope.turn.run.extra_prompt_sections.get("preloaded_skills")
        if not preloaded:
            return goal
        return goal.model_copy(update={
            "description": (
                f"{goal.description}\n\n"
                "## Skill guidance already loaded by the delegating agent\n\n"
                f"{preloaded}"
            ),
        })

    async def handle(self, call: ToolCall, ctx: "RouteContext") -> CoreToolResult:
        """Special-route dispatch (see module docstring): runs the wrapped
        spec as a true child of the calling agent's run — parent
        `RunContext` (trace lineage + spawn-depth guard) and `session_id`
        both propagate, exactly as they do for `spawn_agent`.

        When `share_parent_results` is set, this is also the ONLY place
        that can see the parent's conversation (`ctx.messages`) before the
        child starts — see the class docstring for why that makes it the
        right (and only) place to implement the handoff."""
        goal = self._goal_from_arguments(call.arguments)
        goal = self._inherit_parent_skills(goal, ctx)
        input_files: dict[str, bytes] | None = None
        if self._share_parent_results:
            dependency_results = extract_dependency_results(ctx.messages)
            parent_results = dependency_results + collect_parent_tool_results(ctx.messages)
            digest = format_parent_results_digest(parent_results)
            if digest:
                goal = goal.model_copy(update={
                    "description": (
                        "## Input data from the calling agent (already fetched — do NOT ask "
                        "for it, claim it is unavailable, or fabricate placeholder data)\n\n"
                        + digest + "\n\nThe SAME data, in full and machine-readable, is also "
                        f"pre-loaded at `{PARENT_RESULTS_INPUT_PATH}` as soon as you create a "
                        "sandbox.\n\n## Your task\n\n" + goal.description
                    ),
                })
                payload = parent_results_as_json(parent_results)
                if payload is not None:
                    input_files = {PARENT_RESULTS_INPUT_PATH: payload}

        try:
            with stage_input_files(input_files):
                result = await ctx.runtime.run_child(
                    self._spec,
                    goal,
                    ctx.run_ctx,
                    session_id=ctx.session_id,
                )
        except Exception as e:
            return CoreToolResult(tool_call_id=call.id, name=call.name, content=str(e), is_error=True)
        if not result.success:
            return CoreToolResult(
                tool_call_id=call.id, name=call.name,
                content=result.error or f"{self._name} agent failed", is_error=True,
            )
        return CoreToolResult(
            tool_call_id=call.id, name=call.name,
            content=self._finalize_output(result),
        )

    async def execute(self, **kwargs) -> ToolOutput:
        depth = _agent_tool_depth.get()
        if depth >= MAX_AGENT_TOOL_DEPTH:
            raise ToolError(
                f"agent_as_tool recursion depth ({MAX_AGENT_TOOL_DEPTH}) exceeded calling {self._name!r} — "
                "check for a cycle in your static agent-tool composition."
            )

        token = _agent_tool_depth.set(depth + 1)
        try:
            result = await self._runtime.run_child(
                self._spec, self._goal_from_arguments(kwargs), parent_run_ctx=None,
            )
        finally:
            _agent_tool_depth.reset(token)

        if not result.success:
            return ToolOutput(success=False, error=result.error or f"{self._name} agent failed")
        return ToolOutput(success=True, data=self._finalize_output(result))

    def _with_result_note(self, output: object) -> object:
        """Appends `result_note` to a successful string output — see the
        class docstring for why the note lives in the tool result rather
        than in the calling agent's system prompt. Non-string outputs pass
        through untouched (a structured payload is not prose to annotate)."""
        if not self._result_note or not isinstance(output, str) or not output.strip():
            return output
        return f"{output}\n\n{self._result_note}"
