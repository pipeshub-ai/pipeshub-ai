"""Anti-cheating tool-call guard.

Benchmark runs disable web search and code execution. A run is valid only if
every tool the agent called is a knowledge or loop-planning tool; anything on
the deny list — or, in strict mode, anything unrecognised — invalidates it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from fnmatch import fnmatchcase
from typing import Literal

DEFAULT_ALLOWED_TOOL_PATTERNS: tuple[str, ...] = (
    "retrieval__*",
    "knowledgegraph__*",
    "knowledgehub__*",
    "internal_exploration_agent",
    "final_answer",
    "create_plan",
    "critique_plan",
    "replan",
    "write_todos",
    "task_complete",
    "verify_result",
    "request_review",
    # Progressive tool disclosure (`lazy_tools_wiring.META_TOOL_NAMES`): these
    # only reveal registered tools, and web/code tools are never registered.
    "list_toolsets",
    "fetch_tools",
    "search_tools",
    # Enumerates the skills attached to the agent. Like the meta-tools above it
    # only discloses what exists — no content retrieval, no execution (skills
    # cannot run: `PIPESHUB_ENABLE_CODE_EXECUTION=false`).
    "skills_list",
)

DEFAULT_DENIED_TOOL_PATTERNS: tuple[str, ...] = (
    "*web_search*",
    "*fetch_url*",
    "*browser*",
    "run_code",
    "execute_*",
    "*sandbox*",
    "*coding*",
    "mcp*",
)

ToolVerdict = Literal["allowed", "denied", "unknown"]


class ToolCallGuard:
    def __init__(
        self,
        allowed: Sequence[str] = DEFAULT_ALLOWED_TOOL_PATTERNS,
        denied: Sequence[str] = DEFAULT_DENIED_TOOL_PATTERNS,
        *,
        strict: bool = True,
    ) -> None:
        self._allowed = tuple(p.lower() for p in allowed)
        self._denied = tuple(p.lower() for p in denied)
        self._strict = strict

    def classify(self, tool_name: str) -> ToolVerdict:
        name = tool_name.lower()
        if any(fnmatchcase(name, pattern) for pattern in self._denied):
            return "denied"
        if any(fnmatchcase(name, pattern) for pattern in self._allowed):
            return "allowed"
        return "unknown"

    def violations(self, tool_names: Iterable[str]) -> list[str]:
        """Sorted, de-duplicated names that invalidate the run."""
        bad = {
            name for name in tool_names
            if (verdict := self.classify(name)) == "denied"
            or (verdict == "unknown" and self._strict)
        }
        return sorted(bad)
