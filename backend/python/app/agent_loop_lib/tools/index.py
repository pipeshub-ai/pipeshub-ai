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

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.agent_loop_lib.core.text_scoring import keyword_overlap_score, tokenize
from app.agent_loop_lib.tools.base import ToolSummary

if TYPE_CHECKING:
    from app.agent_loop_lib.tools.registry import ToolRegistry, ToolsetGroup

__all__ = ["ToolMatch", "ToolIndex", "KeywordToolIndex", "ToolsetAwareToolIndex"]


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


# Words that say nothing about which tool is meant. Dropped from a `ToolsetAwareToolIndex` query and
# tool text alike, so "and" no longer counts as much as "tasks".
_STOPWORDS = frozenset({
    "a", "about", "all", "an", "and", "any", "are", "at", "be", "by", "for", "from", "i", "in", "into", "is",
    "it", "its", "me", "my", "of", "on", "or", "our", "please", "some", "that", "the", "their", "these",
    "this", "those", "to", "use", "using", "via", "we", "with", "you", "your",
})
# Toolset-name parts that name no app.
_GENERIC_TOOLSET_WORDS = frozenset({
    "attached", "context", "mcp", "model", "protocol", "server", "servers", "tool", "tools", "toolset",
})


_WORD_RE = re.compile(r"[a-z0-9]+")


def _fold(word: str) -> str:
    """A plural to its singular, conservatively: issues → issue, tasks → task, entries → entry,
    statuses → status, classes → class; "status", "analysis" and short words stay as they are."""
    if len(word) <= 3:
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("sses", "uses")):
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def _ordered_terms(text: str) -> list[str]:
    return [_fold(word) for word in _WORD_RE.findall(text.lower()) if word not in _STOPWORDS]


def _terms(text: str) -> set[str]:
    return set(_ordered_terms(text))


def _toolset_words(group: "ToolsetGroup") -> set[str]:
    """The app a toolset is for: its name (`mcp_linear` → linear), and an MCP group's server name
    ("<server name> — attached MCP server tools"). Other descriptions ("Jira issue tracking.") say
    what the tools do, which isn't an app's name."""
    named = group.name.replace("_", " ")
    if " — " in group.description:
        named += " " + group.description.split(" — ", 1)[0]
    return {word for word in _terms(named) if len(word) >= 3 and word not in _GENERIC_TOOLSET_WORDS}


class ToolsetAwareToolIndex(ToolIndex):
    """`KeywordToolIndex` for the model's own `search_tools` call, which names the app as often as
    the action ("list open Linear issues and search ClickUp tasks"):

    - plurals are folded and stopwords dropped, on both sides;
    - a query word that names a toolset ("linear", "clickup") picks that toolset, and the words
      around it are what its tools are ranked by: "list open Linear issues" for Linear, "search
      ClickUp tasks" for ClickUp. The named apps' best tools come first, taking turns in the order
      the query names them; then everything else that matches. A query that is only an app's name
      lists that app's tools;
    - between tools that match as many words, the one whose name the query covers better wins
      (`list_issues` over `list_issue_labels`).

    Not shared with `tool_preloading`, whose thresholds are tuned to `KeywordToolIndex`'s scores.
    """

    async def search(self, registry: "ToolRegistry", query: str, limit: int = 5) -> list[ToolMatch]:
        terms = _ordered_terms(query)
        if not terms:
            return []
        membership = _toolset_membership(registry)
        words_of_tool: dict[str, set[str]] = {}
        for group in registry.toolsets():
            words = _toolset_words(group)
            for tool_name in group.tool_names:
                words_of_tool.setdefault(tool_name, set()).update(words)
        app_words = set().union(*words_of_tool.values()) if words_of_tool else set()
        mentions = _mentions(terms, app_words)
        summaries = registry.discover()

        def ranked(wanted: set[str], tools: list[ToolSummary]) -> list[tuple[float, ToolSummary]]:
            scored = [(_toolset_score(wanted, s, words_of_tool.get(s.name, set())), s) for s in tools]
            scored = [pair for pair in scored if pair[0] > 0]
            scored.sort(key=lambda pair: pair[0], reverse=True)
            return scored

        queues = [
            ranked(wanted, [s for s in summaries if words_of_tool.get(s.name, set()) & names])
            for names, wanted in mentions
        ]
        picked: list[tuple[float, ToolSummary]] = []
        chosen: set[str] = set()
        while len(picked) < limit and any(queues):
            for queue in queues:
                while queue and queue[0][1].name in chosen:
                    queue.pop(0)
                if queue and len(picked) < limit:
                    score, summary = queue.pop(0)
                    picked.append((score, summary))
                    chosen.add(summary.name)
        if other_words := set(terms) - app_words:
            rest = ranked(other_words, [s for s in summaries if s.name not in chosen])
            picked += rest[: max(0, limit - len(picked))]
        return [
            ToolMatch(summary=summary, relevance=round(score, 3), toolset=membership.get(summary.name))
            for score, summary in picked
        ]


def _mentions(terms: list[str], app_words: set[str]) -> list[tuple[set[str], set[str]]]:
    """(the app words of one mention, the words it ranks by), in query order. Adjacent app words
    ("atlassian rovo") are one mention; the other words go to the nearest mention, and a word
    halfway between two to the earlier one ("Linear issues ClickUp tasks")."""
    spots: list[tuple[int, int, set[str]]] = []
    for i, term in enumerate(terms):
        if term not in app_words:
            continue
        if spots and spots[-1][1] == i - 1:
            first, _, names = spots[-1]
            spots[-1] = (first, i, names | {term})
        else:
            spots.append((i, i, {term}))
    mentions: list[tuple[set[str], set[str]]] = []
    for n, (first, last, names) in enumerate(spots):
        lower = (spots[n - 1][1] + first) / 2 if n else -1
        upper = (last + spots[n + 1][0]) / 2 if n + 1 < len(spots) else len(terms)
        wanted = {term for i, term in enumerate(terms) if lower < i <= upper and term not in app_words}
        mentions.append((names, wanted))
    return mentions


def _toolset_score(wanted: set[str], summary: ToolSummary, own_words: set[str]) -> float:
    """Share of `wanted` the tool's text has, plus a bonus for its name; 1.0 for every tool of the
    named app when nothing else is wanted."""
    if not wanted:
        return 1.0
    name_terms = _terms(summary.name.replace("_", " ")) - own_words
    corpus = name_terms | _terms(summary.short_description) | _terms(
        " ".join(f"{t.key} {t.value}" for t in summary.tags)
    )
    matched = wanted & corpus
    if not matched:
        return 0.0
    in_name = wanted & name_terms
    score = (len(matched) + 0.5 * len(in_name)) / len(wanted)
    return score + (0.25 * len(in_name) / len(name_terms) if name_terms else 0.0)
