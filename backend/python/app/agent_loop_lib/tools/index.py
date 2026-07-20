"""Tool search ABC — kept separate from `ToolRegistry` (Single
Responsibility, mirroring `modules/providers/skills/index.py`'s split
between `SkillStore` and `SkillIndex`) so a future embedding-backed index
(mirroring `agents/agent_loop/skills/semantic_index.py::SemanticSkillIndex`)
can be swapped in via DI without touching the registry, `search_tools`, or
`tool_preloading` — all three depend on this ABC, never on a concrete
scoring algorithm.

Deliberately query-time only (no `rebuild`/`add_entry`/`remove_entry`,
unlike `SkillIndex`): a `ToolRegistry` already IS the tool catalog's source
of truth — tools arrive via `register_tool`/`register_toolset_provider`,
never through a separate index-CRUD API — so a `ToolIndex` only ever needs
to rank against a live registry snapshot, never maintain its own copy.

`KeywordToolIndex` below is the only implementation today: deterministic,
no-LLM-call token-overlap scoring shared with skills search (see
`core/text_scoring.py`) — the same choice Anthropic's own tool-search
guidance makes (description/name QUALITY matters far more than the search
algorithm's sophistication at this scale), and what `tools/builtin/
lazy_toolsets.py::SearchToolsTool` used inline before this module existed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.agent_loop_lib.core.text_scoring import keyword_overlap_score, tokenize
from app.agent_loop_lib.tools.base import ToolSummary

if TYPE_CHECKING:
    from app.agent_loop_lib.tools.registry import ToolRegistry

__all__ = ["ToolMatch", "ToolIndex", "KeywordToolIndex"]


@dataclass(frozen=True)
class ToolMatch:
    """One ranked search hit — `toolset` is the owning group's name (`None`
    if the tool isn't grouped, i.e. it's an "essential"), included so a
    caller never has to re-derive toolset membership itself."""

    summary: ToolSummary
    relevance: float
    toolset: str | None = None


class ToolIndex(ABC):
    @abstractmethod
    async def search(self, registry: "ToolRegistry", query: str, limit: int = 5) -> list[ToolMatch]:
        """Rank every tool `registry.discover()` returns (including
        provider-backed, not-yet-materialized ones — see `tools/
        provider.py`) against `query`, best match first. An empty/
        whitespace-only `query` should return no matches, not "everything"
        — callers that want the full catalog have `list_toolsets` for
        that."""


def _tool_haystack(summary: ToolSummary) -> tuple[set[str], set[str]]:
    """`(name_tokens, full_corpus)` — name tokens kept separate so a query
    that mentions the tool's actual name can be weighted extra, the same
    tie-breaking convention `FilesystemSkillIndex` uses for skills."""
    name_tokens = tokenize(summary.name.replace("_", " "))
    desc_tokens = tokenize(summary.short_description)
    tag_tokens = tokenize(" ".join(f"{t.key} {t.value}" for t in summary.tags))
    return name_tokens, name_tokens | desc_tokens | tag_tokens


def _score(query_tokens: set[str], summary: ToolSummary) -> float:
    name_tokens, corpus = _tool_haystack(summary)
    score, matched = keyword_overlap_score(query_tokens, corpus)
    if not matched:
        return 0.0
    name_matched = query_tokens & name_tokens
    if name_matched:
        score += 0.5 * (len(name_matched) / len(query_tokens))
    return score


def _toolset_membership(registry: "ToolRegistry") -> dict[str, str]:
    membership: dict[str, str] = {}
    for group in registry.toolsets():
        for tool_name in group.tool_names:
            membership.setdefault(tool_name, group.name)
    return membership


class KeywordToolIndex(ToolIndex):
    async def search(self, registry: "ToolRegistry", query: str, limit: int = 5) -> list[ToolMatch]:
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        membership = _toolset_membership(registry)
        scored = [(_score(query_tokens, summary), summary) for summary in registry.discover()]
        scored = [pair for pair in scored if pair[0] > 0]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [
            ToolMatch(summary=summary, relevance=round(score, 3), toolset=membership.get(summary.name))
            for score, summary in scored[:limit]
        ]
