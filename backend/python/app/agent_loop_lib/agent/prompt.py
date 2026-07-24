from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from app.agent_loop_lib.core.types import Goal, Todo
from app.agent_loop_lib.roles.prompt_template import MODE_GUIDANCE, PromptTemplate

if TYPE_CHECKING:
    from app.agent_loop_lib.agent.spec import AgentSpec
    from app.agent_loop_lib.runtime.runtime import AgentRuntime

__all__ = ["SystemPromptBuilder", "DefaultPromptBuilder", "build_system_prompt", "render_skills_overview"]

"""Layer 1: prompt CONSTRUCTION — how `AgentSpec.system_prompt` becomes the
text actually sent to the model. Deliberately separate from Layer 0
(`agent/spec.py`'s `AgentSpec`, which just HOLDS a system prompt) so a
caller who wants an entirely different assembly strategy (e.g. loading
sections from a CMS, A/B testing instructions, per-tenant templates) can
swap in their own `SystemPromptBuilder` without touching `AgentSpec` or the
turn loop at all.
"""


@runtime_checkable
class SystemPromptBuilder(Protocol):
    """Assembles the per-turn system prompt. Called fresh every turn (cheap:
    string assembly, no I/O) so sections that change over the run — mode,
    toolset overview, todos — stay accurate without the caller rebuilding
    anything else.

    `extra_sections` carries run-scoped overrides an `Agent` accumulates
    during its own run (see `Agent.set_prompt_section`) — e.g. a loop
    strategy or hook wanting to add/replace one named section without
    reaching into `AgentSpec` (which is meant to be reusable, definitional
    state, not run-scoped mutable state).
    """

    def build(
        self,
        spec: "AgentSpec",
        runtime: "AgentRuntime",
        goal: Goal,
        todos: list[Todo],
        extra_sections: dict[str, str],
    ) -> str: ...


class DefaultPromptBuilder:
    """Today's named-section template: identity, goal brief, toolset
    overview, skills overview, todos, mode, output style. This is what
    every `AgentSpec` gets when `system_prompt` is a plain string."""

    def build(
        self,
        spec: "AgentSpec",
        runtime: "AgentRuntime",
        goal: Goal,
        todos: list[Todo],
        extra_sections: dict[str, str],
    ) -> str:
        template = PromptTemplate()
        identity = spec.system_prompt if isinstance(spec.system_prompt, str) else ""
        template.set("identity", identity)
        template.set("goal_brief", _render_goal_brief(goal))
        template.set("toolset_overview", _render_toolset_overview(runtime))
        template.set("skills_overview", render_skills_overview(runtime))
        template.set("todos", _render_todos(todos))
        template.set("mode", MODE_GUIDANCE.get(spec.mode, ""))
        template.set("style", f"[Output style: {spec.output_style}]" if spec.output_style else None)

        # Spec-level sections first, then run-scoped overrides on top — set()
        # overwrites by name, so a caller can even replace a builtin section
        # like "mode" if they want.
        for name, content in spec.extra_prompt_sections.items():
            template.set(name, content)
        for name, content in extra_sections.items():
            template.set(name, content)

        return template.render(spec.prompt_section_order)


def build_system_prompt(
    spec: "AgentSpec",
    runtime: "AgentRuntime",
    goal: Goal,
    todos: list[Todo] | None = None,
    extra_sections: dict[str, str] | None = None,
) -> str:
    """Resolve `spec.system_prompt` to its builder (the `DefaultPromptBuilder`
    when it's a plain string, or the caller-supplied `SystemPromptBuilder`
    otherwise) and render this turn's system prompt."""
    todos = todos or []
    extra_sections = extra_sections or {}
    source = spec.system_prompt
    if isinstance(source, str) or source is None:
        return DefaultPromptBuilder().build(spec, runtime, goal, todos, extra_sections)
    return source.build(spec, runtime, goal, todos, extra_sections)


def _render_goal_brief(goal: Goal) -> str:
    if not goal.description and not goal.requirements and not goal.success_criteria:
        return ""
    lines = [f"Goal: {goal.description}"] if goal.description else []
    if goal.requirements:
        lines.append("Requirements: " + "; ".join(goal.requirements))
    if goal.success_criteria:
        lines.append("Success criteria: " + "; ".join(goal.success_criteria))
    if goal.constraints:
        lines.append("Constraints: " + "; ".join(goal.constraints))
    return "\n".join(lines)


_STATUS_MARKER = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}


def _render_todos(todos: list[Todo] | None) -> str:
    if not todos:
        return ""
    lines = ["Current task list:"]
    for t in todos:
        marker = _STATUS_MARKER.get(t.status.value, "[ ]")
        lines.append(f"{marker} {t.content}")
    return "\n".join(lines)


def _render_toolset_overview(runtime: "AgentRuntime") -> str:
    registry = runtime.tool_registry if runtime is not None else None
    if registry is None or not registry.has_toolsets():
        return ""
    lines = [
        "Additional capability groups are available. Call list_toolsets(toolset) "
        "for a one-line description of every tool inside one, or fetch_tools(toolset) "
        "to load the real schemas directly:"
    ]
    _render_toolset_tree(registry.toolset_overview(), lines, indent=0)
    return "\n".join(lines)


def _render_toolset_tree(entries: list[dict], lines: list[str], *, indent: int) -> None:
    """Renders `ToolRegistry.toolset_overview()`'s tree, nesting child
    groups (e.g. a "connectors" category's "jira"/"slack"/...) one level
    deeper per generation. Flat (non-hierarchical) registries never carry
    a `"children"` key, so this recurses exactly once for them."""
    prefix = "  " * indent
    for entry in entries:
        lines.append(f"{prefix}- {entry['name']}: {entry['description']} ({entry['tool_count']} tool(s))")
        children = entry.get("children")
        if children:
            _render_toolset_tree(children, lines, indent=indent + 1)


def render_skills_overview(runtime: "AgentRuntime") -> str:
    """Level-1 progressive disclosure (agentskills.io spec): only name +
    description (or, above `catalog_render_limit`, just a category tree)
    ever ship in the prompt — the full body is fetched on demand via the
    `load_skill` tool. Reads `SkillManager.catalog_snapshot()`, a SYNC,
    in-memory read model kept current by the manager — this function is
    called fresh every turn (see `DefaultPromptBuilder.build`) and must
    stay pure string assembly, no I/O. Public (not builder-private) so
    other `SystemPromptBuilder` implementations — e.g. PipesHub's
    `PipesHubPromptBuilder` — can render the identical section instead of
    duplicating this logic."""
    manager = runtime.skills if runtime is not None else None
    if manager is None:
        return ""
    catalog = manager.catalog_snapshot()
    if not catalog:
        return ""

    limit = manager.config.catalog_render_limit
    if len(catalog) <= limit:
        lines = [
            "Skills available via load_skill(name). IMPORTANT: do NOT load skills "
            "upfront or at the start of a conversation. Only call load_skill immediately "
            "before you execute the specific step the skill covers — after all prerequisite "
            "work (data gathering, tool calls, analysis) is complete. Think about what "
            "the task requires, do all the preparatory steps first, then load the skill "
            "right before the step that needs it. "
        ]
        for m in sorted(catalog, key=lambda m: m.name):
            lines.append(f"- {m.name}: {m.description}")
        return "\n".join(lines)

    categories: dict[str, int] = {}
    for m in catalog:
        categories[m.category or "uncategorized"] = categories.get(m.category or "uncategorized", 0) + 1
    lines = [
        f"{len(catalog)} skills are available, grouped by category — use skill_search(query) or "
        "skills_list(category=...) to find one. Do NOT load skills upfront or at the start of a "
        "conversation; call load_skill(name) only immediately before the step that needs it, "
        "after all prerequisite work is done:",
    ]
    for category, count in sorted(categories.items()):
        lines.append(f"- {category} ({count})")
    return "\n".join(lines)
