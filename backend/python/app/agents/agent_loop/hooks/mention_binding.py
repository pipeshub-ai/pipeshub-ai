"""``mention_binding``: PRE_TOOL_USE hook that resolves pronouns ("they",
"that department", "it") in a knowledge-graph search query against recently
mentioned entities, so the agent doesn't have to re-state a full entity name
every turn.

Deterministic, no LLM — a small recency-ordered mention map plus a regex
scan for referring pronouns. Ambiguous references (two-or-more equally
recent, distinct-type mentions) are surfaced as candidates appended to the
query text rather than silently guessed, per the plan's "if ambiguous return
candidates" requirement; unambiguous ones are inlined directly so the
downstream search/entity-filter tool sees a self-contained query with no
special-cased plumbing.

Population side: ``remember_entity_mentions`` is called by
``ops/entity_search.py`` (``resolve_entity_filters`` tool) after every
resolved entity lookup — see that module's docstring. Kept as a plain
function (not a hook) since it is invoked from tool-execution code, not
from the middleware pipeline.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import ToolCallContext
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware, Next
    from app.agents.agent_loop.context import AgentContext

_MENTION_STATE_KEY = "_kg_mentions"
_MAX_MENTIONS = 20

# Scoped to the tools whose query text benefits from entity substitution —
# same flat names entity_filter.py targets (see that module for why these
# two, and not navigate/list_files, which are structural/name-based rather
# than semantic-query based).
_SCOPED_TOOL_NAMES = frozenset({"knowledgegraph__search", "knowledgegraph__resolve_entity_filters"})

# Word-boundary pronoun scan — deliberately conservative (only bare
# pronouns, not every determiner) to keep false-positive substitutions rare;
# a missed reference just means no auto-substitution, not a wrong one.
_PRONOUN_RE = re.compile(
    r"\b(it|they|them|this|that|these|those|he|she|him|her)\b", re.IGNORECASE,
)


def remember_entity_mentions(state: dict[str, Any], entities: list[dict[str, Any]]) -> None:
    """Push resolved entities onto the recency-ordered mention map.

    Deduplicates by ``entityId`` (re-mentioning moves it to the most-recent
    position) and caps the map at ``_MAX_MENTIONS`` so a long conversation
    doesn't grow this unbounded.
    """
    if not entities:
        return
    mentions: list[dict[str, Any]] = state.setdefault(_MENTION_STATE_KEY, [])
    seen_ids = {m.get("id") for m in mentions}
    for entity in entities:
        entity_id = entity.get("entityId")
        name = entity.get("name")
        if not entity_id or not name:
            continue
        if entity_id in seen_ids:
            mentions[:] = [m for m in mentions if m.get("id") != entity_id]
        mentions.append({
            "id": entity_id,
            "name": name,
            "type": entity.get("entityType"),
        })
        seen_ids.add(entity_id)
    if len(mentions) > _MAX_MENTIONS:
        del mentions[: len(mentions) - _MAX_MENTIONS]


def _resolve_reference(mentions: list[dict[str, Any]]) -> str | None:
    """Build the query-text suffix for a detected pronoun reference.

    Unambiguous (one most-recent mention, or the two most recent share the
    same entity — nothing to disambiguate): inline that entity's name
    directly. Ambiguous (the two most recent mentions are distinct
    entities): list both as candidates instead of guessing.
    """
    if not mentions:
        return None
    latest = mentions[-1]
    if len(mentions) == 1:
        return f"(likely referring to: {latest['name']})"
    prior = mentions[-2]
    if prior.get("id") == latest.get("id"):
        return f"(likely referring to: {latest['name']})"
    return f"(referring to one of: {latest['name']} or {prior['name']} — check which fits)"


def mention_binding(context: "AgentContext") -> "Middleware[ToolCallContext]":
    """PRE_TOOL_USE hook factory closing over the per-request `AgentContext`
    (same pattern as `result_accumulation`/`entity_filter_resolution`)."""

    async def _middleware(ctx: "ToolCallContext", next_fn: "Next") -> None:
        try:
            from app.agents.agent_loop.hooks._tool_naming import resolve_tool_name

            tool_name = resolve_tool_name(ctx)
            if tool_name not in _SCOPED_TOOL_NAMES:
                await next_fn()
                return

            query = ctx.tool_input.get("query")
            if not query or not isinstance(query, str) or not _PRONOUN_RE.search(query):
                await next_fn()
                return

            mentions = context.tool_state.get(_MENTION_STATE_KEY) or []
            suffix = _resolve_reference(mentions)
            if suffix:
                ctx.tool_input["query"] = f"{query} {suffix}"
        except Exception:
            pass
        await next_fn()

    return _middleware


__all__ = ["mention_binding", "remember_entity_mentions"]
