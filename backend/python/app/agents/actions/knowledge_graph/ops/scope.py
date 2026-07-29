"""KnowledgeScope — the single, authoritative scope resolver.

Replaces three divergent inline scope-derivation blocks:
  - knowledge_graph._get_scoping (lines 147-176)
  - knowledge_hub.list_files     (lines 301-347)
  - retrieval filter-group builder (lines 288-362)

All three checked the same three priority sources in the same order but
each differed in return type, sentinel handling, and catalog fallback.
This module is the canonical implementation; call sites import
``resolve_scope`` and delegate entirely.

Security invariant (unchanged from each original):
  The returned scope is always a *subset* of what the agent's configured
  knowledge grants.  ``narrow_to`` never widens — if the caller passes ids
  not in scope the method falls back to the full agent scope rather than
  expanding to org-wide records.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from app.modules.agents.qna.chat_state import ChatState

logger = logging.getLogger(__name__)

_NO_KB_SENTINEL = "NO_KB_SELECTED"


# ---------------------------------------------------------------------------
# Core dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KnowledgeScope:
    """Immutable, validated agent scope resolved from ChatState."""

    app_ids: tuple[str, ...]
    kb_ids: tuple[str, ...]

    def is_empty(self) -> bool:
        return not self.app_ids and not self.kb_ids

    def narrow_to(self, requested: Sequence[str] | None) -> "KnowledgeScope":
        """Return a scope restricted to *requested* ids.

        Rules:
        - ``None`` / empty → return self (no narrowing requested).
        - Intersection non-empty → return that intersection.
        - Intersection empty (hallucinated ids) → return self as fallback
          so the search space is never accidentally empty.
        """
        if not requested:
            return self
        requested_set = frozenset(requested)
        new_apps = tuple(a for a in self.app_ids if a in requested_set)
        new_kbs = tuple(k for k in self.kb_ids if k in requested_set)
        if not new_apps and not new_kbs:
            return self
        return KnowledgeScope(app_ids=new_apps, kb_ids=new_kbs)

    def to_filter_groups(self) -> dict[str, list[str]]:
        """Build the ``filter_groups`` dict the retrieval service expects.

        Empty KB list means ``[]`` — no KB restriction.  The ``NO_KB_SELECTED``
        sentinel is only used in single-source fan-out calls (see
        ``to_filter_groups_for_source``), where scoping to one app must
        explicitly exclude KB results.
        """
        return {
            "apps": list(self.app_ids),
            "kb": list(self.kb_ids),
        }

    def to_filter_groups_for_source(
        self,
        app_id: str | None = None,
        kb_id: str | None = None,
        *,
        placeholder_agent: bool = False,
    ) -> dict[str, list[str]]:
        """Single-source filter group for fan-out searches.

        When isolating one app, use ``NO_KB_SELECTED`` so the retrieval
        service does not mix in KB results (unless this is a placeholder agent,
        which has no curated KB filter and should not restrict the KB side).
        """
        no_kb = [] if placeholder_agent else [_NO_KB_SENTINEL]
        if app_id:
            return {"apps": [app_id], "kb": no_kb}
        if kb_id:
            return {"apps": [], "kb": [kb_id]}
        return self.to_filter_groups()


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def _clean_kb(raw: list[str]) -> tuple[str, ...]:
    return tuple(k for k in raw if k and k != _NO_KB_SENTINEL)


def derive_scope(state: "ChatState") -> KnowledgeScope:
    """Synchronous scope derivation — priorities 1-3 only (no I/O).

    Used by navigate() and lookup_record() which are already inside async
    methods and need only the cached state data.  list_files() and the
    retrieval builder use the async ``resolve_scope`` when they need the
    catalog fallback.
    """
    from app.modules.agents.qna.chat_state import (
        _extract_kb_app_ids,
        _extract_knowledge_connector_ids,
    )

    app_ids: list[str] = list(state.get("apps") or [])
    raw_kb: list[str] = list(state.get("kb") or [])
    kb_ids: list[str] = list(_clean_kb(raw_kb))

    if not app_ids or not kb_ids:
        filters: dict = state.get("filters") or {}
        if not app_ids:
            app_ids = list(filters.get("apps") or [])
        if not kb_ids:
            kb_ids = list(_clean_kb(filters.get("kb") or []))

    if not app_ids and not kb_ids:
        knowledge = state.get("agent_knowledge") or []
        app_ids = _extract_knowledge_connector_ids(knowledge)
        kb_ids = _extract_kb_app_ids(knowledge)

    return KnowledgeScope(app_ids=tuple(app_ids), kb_ids=tuple(kb_ids))


async def resolve_scope(
    state: "ChatState",
    *,
    allow_catalog_fallback: bool = False,
) -> KnowledgeScope:
    """Derive the agent's knowledge scope from ChatState.

    Priority order (same as each original implementation):
    1. ``state["apps"]`` / ``state["kb"]`` — pre-extracted by create_chat_state.
    2. ``state["filters"]["apps"]`` / ``state["filters"]["kb"]`` — route filter.
    3. Re-derive from ``state["agent_knowledge"]`` (legacy fallback).
    4. If ``allow_catalog_fallback`` and still empty: query the ConnectorCatalog
       (the path knowledge_hub.list_files took in chat-mode; navigate/search do
       not need it because they handle the empty-scope case differently).

    Returns an empty ``KnowledgeScope`` for chat-mode agents that have no
    per-agent knowledge configured.  Callers decide what "empty" means for them.
    """
    scope = derive_scope(state)
    if scope.is_empty() and allow_catalog_fallback:
        catalog_scope = await _catalog_fallback(state)
        if catalog_scope is not None:
            return catalog_scope
    return scope


async def _catalog_fallback(state: "ChatState") -> KnowledgeScope | None:
    """Last-resort: derive scope from the ConnectorCatalog for chat-mode agents."""
    if not state.get("has_knowledge"):
        return None
    graph_provider = state.get("graph_provider")
    if not graph_provider:
        return None
    try:
        from app.agents.actions.knowledge_graph.catalog import ConnectorCatalog

        user_id = state.get("user_id", "")
        org_id = state.get("org_id", "")
        user = await graph_provider.get_user_by_user_id(user_id=user_id)
        user_key = (user.get("_key") or user.get("id")) if user else None
        if not user_key:
            return None
        catalog = await ConnectorCatalog.build(
            state,
            graph_provider=graph_provider,
            user_key=user_key,
            org_id=org_id,
        )
        app_ids = tuple(catalog.connector_ids())
        return KnowledgeScope(app_ids=app_ids, kb_ids=())
    except Exception as err:
        logger.warning("resolve_scope: catalog fallback failed: %s", err)
        return None
