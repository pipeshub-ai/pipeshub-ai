from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.agent_loop_lib.hooks.middleware.context import AgentLifecycleContext
from app.agent_loop_lib.tools.index import KeywordToolIndex, ToolIndex

if TYPE_CHECKING:
    from app.agent_loop_lib.tools.index import ToolMatch
    from app.agent_loop_lib.tools.registry import ToolRegistry

"""Intent-based toolset preloading — the DETERMINISTIC half of the
middleware-vs-tool tradeoff (see this project's design brief, and
`skill_preloading.py`'s docstring for the skills-side precedent this
mirrors exactly). `list_toolsets`/`fetch_tools`/`search_tools` are tools:
the model MAY call them, but nothing forces it to, so an obviously relevant
toolset (e.g. a "jira" toolset for a request that clearly needs Jira) can
go unfetched simply because the model didn't think to look. This PRE_AGENT
middleware removes that gap by always searching the tool catalog against
the goal and, by relevance band, either unlocking a whole toolset (as if
`fetch_tools` had already been called for it) or injecting a one-line
pointer — BEFORE the first turn, so the model never has to spend a turn
discovering what determinism could have handed it up front.

Registered by whoever wires lazy toolsets (see the module's own docstring
in `tools/builtin/lazy_toolsets.py` for the probabilistic half) — a no-op,
never an error, when the registry has no toolsets, the goal has no
description, or the agent's tool grant is eager (every granted tool is
already fully visible from turn 0 in that mode — see `AgentSpec.
tool_disclosure` — so there is nothing for preloading to usefully unlock).
"""

logger = logging.getLogger(__name__)


def tool_preloading(
    *,
    index: ToolIndex | None = None,
    preload_threshold: float = 0.75,
    mention_threshold: float = 0.4,
    top_k: int = 10,
):
    """PRE_AGENT middleware factory. Thresholds are relevance scores from
    `ToolIndex.search` (0..1, higher = more relevant) — tune per deployment;
    the defaults favor precision (few, confidently-relevant unlocks) over
    recall, since an over-eager unlock grows every subsequent turn's tool
    schema payload for the rest of the run, not just once.

    `index` should be the SAME `ToolIndex` instance passed to
    `SearchToolsTool` for one deployment, so preloading and the model's own
    `search_tools` calls rank identically — defaults to a fresh
    `KeywordToolIndex()` (stateless, so a fresh instance is equivalent to
    sharing one) when not given.
    """
    search_index = index or KeywordToolIndex()

    async def _middleware(ctx: AgentLifecycleContext, next_fn) -> None:
        scope = ctx.scope
        if scope is None or ctx.goal is None or not ctx.goal.description:
            await next_fn()
            return

        spec = scope.spec
        if spec.tool_names and spec.tool_disclosure != "lazy":
            # Eager grant: every named tool is already fully visible from
            # turn 0 (see `agent/tool_loop.py::tool_schemas_for_turn`) —
            # nothing for preloading to unlock, and injecting toolset
            # pointers here would be misleading (there's no fetch_tools
            # gate to point at).
            await next_fn()
            return

        registry = scope.runtime.tool_registry
        if registry is None or not registry.has_toolsets():
            await next_fn()
            return

        try:
            matches = await search_index.search(registry, ctx.goal.description, limit=top_k)
        except Exception:
            logger.exception("tool_preloading: search failed, skipping preload")
            await next_fn()
            return

        if scope.visible_tools is None:
            from app.agent_loop_lib.agent.tool_loop import initial_visible_tools

            scope.visible_tools = initial_visible_tools(spec, scope.runtime)

        grant = set(spec.tool_names) if spec.tool_names else None
        unlocked, pointers, unlock_names = _select_toolsets(
            registry, matches, grant=grant,
            preload_threshold=preload_threshold, mention_threshold=mention_threshold,
        )

        if unlock_names:
            await registry.materialize_many(unlock_names)
            scope.visible_tools = scope.visible_tools | set(unlock_names)

        section = _render_preload_section(unlocked, pointers)
        if section:
            scope.extra_prompt_sections["preloaded_tools"] = section
        else:
            scope.extra_prompt_sections.pop("preloaded_tools", None)

        await next_fn()

    return _middleware


def _best_match_per_toolset(matches: "list[ToolMatch]") -> dict[str, "ToolMatch"]:
    """Reduces tool-level search matches to one (highest-relevance) match
    per owning toolset — preloading decides whether to unlock a whole
    TOOLSET, not individual tools within it, so only the toolset's best
    showing matters. Matches with no toolset (essentials — already always
    visible) are skipped; there is nothing for preloading to do for them."""
    best: dict[str, "ToolMatch"] = {}
    for match in matches:
        if match.toolset is None:
            continue
        current = best.get(match.toolset)
        if current is None or match.relevance > current.relevance:
            best[match.toolset] = match
    return best


def _select_toolsets(
    registry: "ToolRegistry",
    matches: "list[ToolMatch]",
    *,
    grant: set[str] | None,
    preload_threshold: float,
    mention_threshold: float,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[str]]:
    """Returns `(unlocked, pointers, unlock_names)`:
    - `unlocked` / `pointers`: `(toolset_name, toolset_description)` pairs
      for the prompt section, split by relevance band.
    - `unlock_names`: every tool name to add to `visible_tools` for the
      `unlocked` toolsets — already intersected with `grant` (the
      permission ceiling; a toolset with nothing left after intersection is
      dropped entirely, from both `unlocked` and `unlock_names`).
    """
    unlocked: list[tuple[str, str]] = []
    pointers: list[tuple[str, str]] = []
    unlock_names: list[str] = []
    groups_by_name = {g.name: g for g in registry.toolsets()}

    for toolset_name, best in _best_match_per_toolset(matches).items():
        group = groups_by_name.get(toolset_name)
        if group is None:
            continue
        names = registry.tools_in_toolset(toolset_name)
        if grant is not None:
            names = [n for n in names if n in grant]
        if not names:
            continue
        if best.relevance >= preload_threshold:
            unlocked.append((group.name, group.description))
            unlock_names.extend(names)
        elif best.relevance >= mention_threshold:
            pointers.append((group.name, group.description))

    return unlocked, pointers, unlock_names


def _render_preload_section(
    unlocked: list[tuple[str, str]], pointers: list[tuple[str, str]],
) -> str:
    parts: list[str] = []
    if unlocked:
        lines = "\n".join(f"- {name}: {description}" for name, description in unlocked)
        parts.append(
            "The following toolset(s) look directly relevant to this request and have "
            "already been loaded — their tools are callable right now, no need to call "
            "fetch_tools for them:\n" + lines
        )
    if pointers:
        lines = "\n".join(f"- {name}: {description}" for name, description in pointers)
        parts.append(
            "The following toolset(s) may also be relevant — call fetch_tools(toolset) "
            "if one turns out to apply:\n" + lines
        )
    return "\n\n".join(parts)


__all__ = ["tool_preloading"]
