"""`SurfacePolicy`: which capabilities a run may be offered, as data the
factory, tool loader and prompt builder all consult.

`AgentContext.surface_policy is None` means the full surface — every mode
and entry point that never sets one (custom agents, `web_search`/`agent`
chat) composes exactly as before. A chat mode that sets one narrows the
grant AND the prompt from the same object, so a withheld tool can never be
described by a surviving prompt section, and vice versa.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent_loop_lib.tools.base import Tool

# Prompt-section keys `PipesHubPromptBuilder` checks via `withholds()`.
WORKPLACE_IDENTITY = "workplace_identity"
CODE_EXECUTION = "code_execution"
SKILLS_OVERVIEW = "skills_overview"
ARTIFACT_REMINDER = "artifact_reminder"
WEB_CITATION_MARKER = "web_citation_marker"
WRITE_ACTION_RULE = "write_action_rule"
WRITE_TOOL_GUIDANCE = "write_tool_guidance"
ACTION_WORKED_EXAMPLES = "action_worked_examples"

# Leading verbs of connector tools that only read. Anything else is treated
# as side-effecting: a new verb has to be added here before a focused surface
# will offer it, rather than a new write tool slipping through by default.
_READ_VERBS = frozenset({
    "search", "get", "list", "fetch", "find", "read", "lookup", "query",
    "describe", "retrieve", "view", "show", "browse", "navigate", "count",
})
_WRITE_CATEGORIES = frozenset({"write", "execute"})
_ELEVATED_RISK = frozenset({"medium", "high"})


def is_read_only_tool(tool: "Tool") -> bool:
    tags = {(tag.key, tag.value) for tag in tool.tags}
    if any(key == "category" and value in _WRITE_CATEGORIES for key, value in tags):
        return False
    if any(key == "risk" and value in _ELEVATED_RISK for key, value in tags):
        return False
    if ("category", "read") in tags:
        return True
    leaf = tool.name.rsplit("__", 1)[-1].rsplit(".", 1)[-1]
    return leaf.split("_", 1)[0].lower() in _READ_VERBS


@dataclass(frozen=True)
class SurfacePolicy:
    """Capabilities a run may be offered; `False` withholds both the tools
    and every prompt section that only makes sense with them."""

    name: str
    code_execution: bool = True
    skills: bool = True
    artifacts: bool = True
    image_generation: bool = True
    web: bool = True
    mcp: bool = True
    write_actions: bool = True
    # `search_tools`' global catalog fallback advertises toolsets this run
    # cannot load; under eager disclosure it has nothing else to find.
    tool_discovery: bool = True
    answers_from_knowledge_only: bool = False

    def withheld_toolsets(self) -> frozenset[str]:
        """Tool-group names the loader must skip outright."""
        names: set[str] = set()
        if not self.artifacts:
            names.add("artifacts")
        if not self.image_generation:
            names.add("image_generator")
        return frozenset(names)

    def withheld_capabilities(self) -> tuple[str, ...]:
        flags = (
            ("code_execution", self.code_execution), ("skills", self.skills),
            ("artifacts", self.artifacts), ("image_generation", self.image_generation),
            ("web", self.web), ("mcp", self.mcp), ("write_actions", self.write_actions),
            ("tool_discovery", self.tool_discovery),
        )
        return tuple(name for name, allowed in flags if not allowed)

    def withheld_sections(self) -> tuple[str, ...]:
        sections: list[str] = []
        if self.answers_from_knowledge_only:
            sections.append(WORKPLACE_IDENTITY)
        if not self.code_execution:
            sections.append(CODE_EXECUTION)
        if not self.skills:
            sections.append(SKILLS_OVERVIEW)
        if not self.artifacts:
            sections.append(ARTIFACT_REMINDER)
        if not self.web:
            sections.append(WEB_CITATION_MARKER)
        if not self.write_actions:
            sections.extend((WRITE_ACTION_RULE, WRITE_TOOL_GUIDANCE, ACTION_WORKED_EXAMPLES))
        return tuple(sections)

    def withholds(self, section: str) -> bool:
        return section in self.withheld_sections()

    def permits_tool(self, tool: "Tool") -> bool:
        """Per-tool gate for connector toolsets; internal toolsets are
        withheld whole via `withheld_toolsets()`."""
        return self.write_actions or is_read_only_tool(tool)


__all__ = [
    "ACTION_WORKED_EXAMPLES",
    "ARTIFACT_REMINDER",
    "CODE_EXECUTION",
    "SKILLS_OVERVIEW",
    "SurfacePolicy",
    "WEB_CITATION_MARKER",
    "WORKPLACE_IDENTITY",
    "WRITE_ACTION_RULE",
    "WRITE_TOOL_GUIDANCE",
    "is_read_only_tool",
]
