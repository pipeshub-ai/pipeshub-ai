"""search_semantic: find code by what it does, not by its name.

A user names a feature ("where a failed job is retried"), not a symbol.
Identifier search needs the name and text search a guessed word; this runs the
knowledge search's hybrid query (dense + the symbols' subtokens) over the
repo's code and answers with symbol addresses, so the graph tools can take
over from the hit.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .ops import (
    _BLOCKS,
    _RECORD_GROUPS,
    TEST_ROLE,
    SymbolRef,
    _repo_matches,
    _unwrap,
    attach_file_paths,
    get_record_roles,
    repo_names,
)
from .repos import repo_not_found_message

if TYPE_CHECKING:
    from app.modules.retrieval.retrieval_service import RetrievalService
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

logger = logging.getLogger(__name__)

__all__ = ["DEFAULT_SEMANTIC_LIMIT", "search_semantic_impl"]

DEFAULT_SEMANTIC_LIMIT = 15
MAX_SEMANTIC_LIMIT = 50
_MAX_QUERY = 500
# Passages fetched per symbol returned: several passages can be one long symbol,
# and test files are dropped after the search.
_OVERFETCH = 3
# Blocks one code file can hold; a generated file past this is read partially.
_BLOCKS_PER_FILE = 2000
# Retrieval statuses that mean the search ran; any other says why it did not.
# A missing status is a plain result.
_NO_HITS_STATUSES = {None, "success", "empty_response", "accessible_records_not_found"}


async def search_semantic_impl(
    *,
    graph_provider: IGraphDBProvider,
    retrieval_service: RetrievalService | None,
    connector_id: str,
    org_id: str,
    user_id: str,
    query: str,
    repo: str | None = None,
    limit: int = DEFAULT_SEMANTIC_LIMIT,
    include_tests: bool = False,
) -> dict[str, Any]:
    query = (query or "").strip()
    if not query:
        return {"error": "query is required"}
    if len(query) > _MAX_QUERY:
        return {"error": f"query is longer than {_MAX_QUERY} characters"}
    if retrieval_service is None:
        return {"error": "Semantic search is not available here; use search_code."}
    limit = max(1, min(int(limit or DEFAULT_SEMANTIC_LIMIT), MAX_SEMANTIC_LIMIT))

    filter_groups: dict[str, list[str]] = {"apps": [connector_id]}
    if repo:
        # Scoped in the search itself, so other repos' hits cannot use up the limit.
        groups = await _repo_groups(graph_provider, org_id, connector_id, repo)
        if not groups:
            return {"error": repo_not_found_message(repo)}
        filter_groups["recordGroups"] = groups
    async def search(groups: dict[str, list[str]]) -> dict[str, Any]:
        # The search checks the caller's access to every record it returns.
        response = await retrieval_service.search_with_filters(
            queries=[query], user_id=user_id, org_id=org_id,
            filter_groups=groups, limit=limit * _OVERFETCH,
        )
        return response if isinstance(response, dict) else {}

    response = await search(filter_groups)
    if repo and response.get("status") == "selection_not_ready":
        # shortcut: a connector whose vectors lack group membership cannot be scoped
        # by repo yet; the repo filter below still applies, so hits can come up short.
        response = await search({"apps": [connector_id]})
    hits = _code_hits(response)
    if not hits:
        message = response.get("message")
        # A search that did not run is not "nothing matched".
        if response.get("status") not in _NO_HITS_STATUSES:
            return {"error": message or "The search failed; try again or use search_code."}
        return {"connector_id": connector_id, "matches": [], "total": 0,
                "hint": message or "Nothing matched; try other words, or search_code for a name."}

    blocks_by_hit = await _blocks_for(graph_provider, org_id, connector_id, {r for r, _, _ in hits})
    seen: set[str] = set()
    ranked: list[tuple[dict[str, Any], float]] = []
    for record_id, index, score in hits:
        block = blocks_by_hit.get((record_id, index))
        key = block.get("_key") or block.get("id") if block else None
        if block is None or key in seen:
            continue
        seen.add(key)
        ranked.append((block, score))

    hidden = 0
    if not include_tests:
        roles = await get_record_roles(graph_provider, org_id, {b.get("recordId") for b, _ in ranked})
        kept = [(b, s) for b, s in ranked if roles.get(b.get("recordId")) != TEST_ROLE]
        hidden = len(ranked) - len(kept)
        ranked = kept
    if repo:
        names = await repo_names(graph_provider, org_id, (b.get("recordGroupId") for b, _ in ranked))
        ranked = [(b, s) for b, s in ranked if _repo_matches(repo, b.get("recordGroupId"), names)]
    await attach_file_paths(graph_provider, org_id, [b for b, _ in ranked])

    out: dict[str, Any] = {
        "connector_id": connector_id,
        "matches": [{**SymbolRef.from_block(b), "score": round(s, 4)} for b, s in ranked[:limit]],
        "total": len(ranked),
        "truncated": len(ranked) > limit,
    }
    if hidden:
        out["hidden_tests"] = hidden
    return out


async def _repo_groups(
    graph_provider: IGraphDBProvider, org_id: str, connector_id: str, repo: str,
) -> list[str]:
    """The connector's record groups that ``repo`` names."""
    rows = await graph_provider.get_nodes_by_field_in(
        collection=_RECORD_GROUPS, field_name="connectorId", field_values=[connector_id],
        return_fields=["_key"], raise_on_error=True,
    )
    ids = [key for row in map(_unwrap, rows or []) if (key := row.get("_key") or row.get("id"))]
    names = await repo_names(graph_provider, org_id, ids)
    return sorted(g for g in ids if _repo_matches(repo, g, names))


def _code_hits(response: object) -> list[tuple[str, int, float]]:
    """(recordId, blockIndex, score) of each code passage, best first, once each."""
    if not isinstance(response, dict):
        return []
    out: list[tuple[str, int, float]] = []
    seen: set[tuple[str, int]] = set()
    for result in response.get("searchResults") or []:
        metadata = (result or {}).get("metadata") or {}
        record_id, index = metadata.get("recordId"), metadata.get("blockIndex")
        if not record_id or not isinstance(index, int) or metadata.get("isBlockGroup"):
            continue
        if (record_id, index) in seen:
            continue
        seen.add((record_id, index))
        out.append((record_id, index, float(result.get("score") or 0.0)))
    return out


async def _blocks_for(
    graph_provider: IGraphDBProvider, org_id: str, connector_id: str, record_ids: set[str],
) -> dict[tuple[str, int], dict[str, Any]]:
    """The graph block each (record, block index) names: the graph keys blocks by
    qualified name, the vector index by position in the file."""
    out: dict[tuple[str, int], dict[str, Any]] = {}
    for record_id in sorted(record_ids):
        rows = await graph_provider.get_documents_paginated(
            collection=_BLOCKS, limit=_BLOCKS_PER_FILE,
            filters={"orgId": org_id, "connectorId": connector_id, "recordId": record_id},
            sort_field="_key", raise_on_error=True,
        )
        for row in rows or []:
            # The query is already scoped to the org.
            if not row.get("isBlockGroup") and isinstance(row.get("index"), int) and row.get("qualifiedName"):
                out[(record_id, row["index"])] = row
    return out
