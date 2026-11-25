"""search_semantic: find code by what it does, not by its name.

A user names a feature ("where a failed job is retried"), not a symbol.
Identifier search needs the name and text search a guessed word; this runs the
knowledge search's hybrid query (dense + the symbols' subtokens) over the
repo's code and answers with symbol addresses, so the graph tools can take
over from the hit.

A hit is one of three kinds of point: a code block (by ``blockIndex``), a class's
header unit (``unitKind: "class"``, by ``blockGroupIndex``) or a file's summary
(``isRecordSummary``). Blocks and classes answer as symbols; a summary answers
as the file, with the start of its summary.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal

from app.config.constants.arangodb import RecordTypes

from .ops import (
    _BLOCKS,
    _RECORD_GROUPS,
    _RECORDS,
    TEST_ROLE,
    RecordAccess,
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
# What a hit's node needs to be answered, ranked, filtered and located.
_HIT_FIELDS = ["_key", "id", "orgId", "connectorId", "recordId", "recordGroupId", "index", "isBlockGroup",
               "qualifiedName", "kind", "startLine", "endLine"]
_SUMMARY_CHARS = 200
# The hit records' nodes are fetched a few records per query, best hits first, and the fetch stops
# once this many nodes are in hand: a generated file can hold tens of thousands, and one query reads
# its records whole (the provider call takes no row limit), so a query holds few of them.
_FETCH_RECORDS = 5
_FETCH_ROW_CEILING = 20_000
_FETCH_STOPPED = ("Some matches were left out: their files hold too much code to read in one search. "
                  "Narrow it with `repo`, or use search_code for a name you know.")
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

    # The search already checks access; a hit reaches this tool's answer only through a readable record
    # here too, as every other code-graph tool's does (S-7).
    readable = await RecordAccess(graph_provider, org_id, user_id).readable(h[1] for h in hits)
    hits = [h for h in hits if h[1] in readable]
    nodes, groups, fetch_stopped = await _nodes_for(graph_provider, org_id, connector_id,
                                                    list(dict.fromkeys(h[1] for h in hits)))
    # A file whose nodes were not read, or that holds only unnamed code, still has its record's group.
    unplaced = {h[1] for h in hits if h[0] == "file" and h[1] not in groups}
    if unplaced:
        groups.update(await _record_groups(graph_provider, connector_id, unplaced))
    seen: set[str] = set()
    ranked: list[tuple[dict[str, Any], float]] = []
    for kind, record_id, index, score, content in hits:
        node = _node_for(nodes, groups, kind, record_id, index, content)
        key = node.get("_key") or node.get("id") if node else None
        if node is None or key in seen:
            continue
        seen.add(key)
        ranked.append((node, score))

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
        "matches": [{**_match(b), "score": round(s, 4)} for b, s in ranked[:limit]],
        "total": len(ranked),
        "truncated": len(ranked) > limit or fetch_stopped,
    }
    if fetch_stopped:
        out["note"] = _FETCH_STOPPED
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


HitKind = Literal["block", "class", "file"]
# (kind, recordId, block or group index -- None for a file --, score, the point's text)
Hit = tuple[HitKind, str, "int | None", float, str]


def _code_hits(response: object) -> list[Hit]:
    """Each code passage, class unit and file summary, best first, once each. Other group
    points (tables, SQL) are not code and are left out."""
    if not isinstance(response, dict):
        return []
    out: list[Hit] = []
    seen: set[tuple[str, str, int | None]] = set()
    for result in response.get("searchResults") or []:
        metadata = (result or {}).get("metadata") or {}
        record_id = metadata.get("recordId")
        if not record_id:
            continue
        if metadata.get("unitKind") == "class" and isinstance(metadata.get("blockGroupIndex"), int):
            kind, index = "class", metadata["blockGroupIndex"]
        elif metadata.get("isRecordSummary"):
            kind, index = "file", None
        elif isinstance(metadata.get("blockIndex"), int) and not metadata.get("isBlockGroup"):
            kind, index = "block", metadata["blockIndex"]
        else:
            continue
        if (kind, record_id, index) in seen:
            continue
        seen.add((kind, record_id, index))
        out.append((kind, record_id, index, float(result.get("score") or 0.0), str(result.get("content") or "")))
    return out


async def _nodes_for(
    graph_provider: IGraphDBProvider, org_id: str, connector_id: str, record_ids: list[str],
) -> tuple[dict[tuple[str, bool, int], dict[str, Any]], dict[str, str | None], bool]:
    """The graph nodes of the hit records, keyed (recordId, isBlockGroup, index): the graph keys
    blocks by qualified name, the vector index by position in the file. Also each read record's group,
    from any of its nodes, named or not. ``record_ids`` come best hit first and are fetched
    ``_FETCH_RECORDS`` at a time; the last value is True when the fetch stopped at
    ``_FETCH_ROW_CEILING`` before every record was read."""
    out: dict[tuple[str, bool, int], dict[str, Any]] = {}
    groups: dict[str, str | None] = {}
    fetched = 0
    for start in range(0, len(record_ids), _FETCH_RECORDS):
        if fetched >= _FETCH_ROW_CEILING:
            return out, groups, True
        rows = await graph_provider.get_nodes_by_field_in(
            collection=_BLOCKS, field_name="recordId", field_values=record_ids[start:start + _FETCH_RECORDS],
            return_fields=_HIT_FIELDS, raise_on_error=True,
        ) or []
        fetched += len(rows)
        for row in map(_unwrap, rows):
            # `get_nodes_by_field_in` takes no filters, so the scope is re-applied on the rows.
            if row.get("orgId") != org_id or row.get("connectorId") != connector_id:
                continue
            groups.setdefault(row.get("recordId"), row.get("recordGroupId"))
            if isinstance(row.get("index"), int) and row.get("qualifiedName"):
                out[(row["recordId"], bool(row.get("isBlockGroup")), row["index"])] = row
    return out, groups, False


async def _record_groups(
    graph_provider: IGraphDBProvider, connector_id: str, record_ids: set[str],
) -> dict[str, str | None]:
    """Each code file's group from the record itself. The ids are hit records that already passed the
    search's org scope and the access check, so only the connector is re-checked. Other records of a code
    connector (an issue, a pull request) have summaries too, but no file a code tool can read: left out."""
    rows = await graph_provider.get_nodes_by_field_in(
        collection=_RECORDS, field_name="_key", field_values=sorted(record_ids),
        return_fields=["_key", "connectorId", "recordGroupId", "recordType"], raise_on_error=True,
    ) or []
    return {key: row.get("recordGroupId") for row in map(_unwrap, rows)
            if row.get("connectorId") == connector_id and row.get("recordType") == RecordTypes.CODE_FILE.value
            and (key := row.get("_key") or row.get("id"))}


def _node_for(nodes: dict[tuple[str, bool, int], dict[str, Any]], groups: dict[str, str | None],
              kind: HitKind, record_id: str, index: int | None, content: str) -> dict[str, Any] | None:
    """The node a hit answers with. A file is a node of its own, carrying its record's group (from any of
    the record's nodes, ``groups``) so the repo scope applies to it as to a symbol."""
    if kind != "file":
        return nodes.get((record_id, kind == "class", index))
    group = groups.get(record_id)
    if group is None:
        return None
    return {"_key": f"file:{record_id}", "recordId": record_id, "recordGroupId": group, "kind": "file",
            "summary": content[:_SUMMARY_CHARS]}


def _match(node: dict[str, Any]) -> dict[str, Any]:
    if node.get("kind") != "file":
        return SymbolRef.from_block(node)
    out = {"file_path": node.get("filePath"), "kind": "file"}
    if node.get("repo"):
        out["repo"] = node["repo"]
    return {**out, "summary": node["summary"]}
