"""get_neighbour: what a symbol or file reaches, and what reaches it."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import RecordRelations
from app.modules.parsers.code_parser.models import FILLER_KINDS

from .ops import (
    _BLOCKS,
    CODE_RELATIONS,
    HERITAGE_RELATIONS,
    STRUCTURAL_RELATIONS,
    TEST_ROLE,
    RecordAccess,
    SymbolRef,
    _choose_records,
    _degrees,
    _key_of,
    _readable_blocks,
    _readable_keys,
    _records_for_path,
    _repo_matches,
    attach_file_paths,
    coverage_for,
    get_record_roles,
    repo_names,
    resolve_symbol,
)
from .results import touched

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

logger = logging.getLogger(__name__)

__all__ = ["DEFAULT_NEIGHBOR_LIMIT", "IMPACT_RELATIONS", "MAX_NEIGHBOUR_DEPTH", "get_neighbour_impl", "impact_impl", "DEFAULT_IMPACT_DEPTH"]

DEFAULT_NEIGHBOR_LIMIT = 100
# A neighbour walk is for tracing a specific chain, not surveying the repo:
# fan-out compounds per hop, so a deep walk returns more than it explains.
MAX_NEIGHBOUR_DEPTH = 3
# Frontier width per hop while walking past the first. The caller's `limit`
# bounds what comes back; this bounds what is traversed to find it.
_NEIGHBOUR_FANOUT = 200
# Edge rows fetched per hop: per anchor, and in total. The provider's `limit`
# is a cap across the whole frontier, so a flat 200 let one hub with 300
# callers come back as 200 rows and `truncated: false`. Both caps are reported
# when they bite, never applied silently.
_ROWS_PER_ANCHOR = 1000
_HOP_ROW_LIMIT = 5000

# ---------------------------------------------------------------------------
# Tool 1 — callers / callees
# ---------------------------------------------------------------------------

async def _resolve_anchors(
    graph_provider: IGraphDBProvider,
    org_id: str,
    file_path: str,
    qualified_name: str | None,
    connector_id: str,
    *,
    repo: str | None,
    access: RecordAccess,
) -> list[dict[str, Any]]:
    """The symbols a walk starts from: one named symbol, or the whole file.

    Whole-file is the shape a caller has right after a search or a listing —
    a path, and no symbol yet. Without it, "what does this file talk to" costs
    one call per symbol, and the cheap substitute is a capped glob that returns
    a ranked sample of a subtree instead of the file's actual edges.

    Filler spans are ordered last rather than dropped, so the fan-out cap sheds
    a comment before a definition. They stay in because the imports block is one
    of them and it owns the file's IMPORTS_FROM edges -- dropping it would make
    "what does this file depend on" unanswerable at file scope.
    """
    if qualified_name:
        anchor = await resolve_symbol(
            graph_provider, org_id, file_path, qualified_name, connector_id,
            repo=repo, access=access,
        )
        return [anchor] if anchor else []

    record_ids = await _choose_records(
        graph_provider, org_id, await _records_for_path(graph_provider, org_id, file_path),
        connector_id, file_path=file_path, repo=repo, access=access,
    )
    if not record_ids:
        return []
    rows = await graph_provider.get_nodes_by_field_in(
        collection=_BLOCKS, field_name="recordId", field_values=record_ids, raise_on_error=True
    )
    mine = [row for row in (rows or [])
            if row.get("orgId") == org_id and row.get("connectorId") == connector_id]
    mine.sort(key=lambda row: (
        (row.get("kind") or "").lower() in FILLER_KINDS, row.get("startLine") or 0,
    ))
    # One past the cap, so the caller can tell "exactly 200" from "more".
    return mine[:_NEIGHBOUR_FANOUT + 1]


# Candidates a bare name may resolve to before the walk gives up and asks which
# one was meant. Wide enough that a common method name still shows every owner.
_NAME_CANDIDATE_LIMIT = 400


def _bare_name(qualified_name: str) -> str:
    """The symbol's own name, dropping any `kind:` prefix and owner path.

    `method:BaseConnector.notify`, `BaseConnector.notify` and `notify` all name
    the same symbol, and only the first is what the indexer stored. Matching on
    the last segment lets a caller name a symbol it has only heard of, which is
    the whole point of resolving without a file.
    """
    tail = qualified_name.rsplit(":", 1)[-1]
    return tail.rsplit(".", 1)[-1].strip()


async def _resolve_anchors_by_name(
    graph_provider: IGraphDBProvider,
    org_id: str,
    qualified_name: str,
    connector_id: str,
) -> list[dict[str, Any]]:
    """Blocks whose own name matches ``qualified_name``, anywhere in one repo.

    The address rule everywhere else is "copy, do not compose", which leaves a
    caller holding a name but no file with nothing to call: every supplier of
    addresses (a search hit, a listing) is large enough to be compacted out of
    context before it gets used. This is the one lookup that starts from a name,
    which is what `block_text` (``FOR (n:Block) ON EACH [n.name,
    n.qualifiedName]``) was indexed for.

    Returns every match; the caller decides between walking a single hit and
    asking which of several was meant. An exact ``qualifiedName`` match wins
    outright, so a fully-spelled address never turns into a disambiguation.
    """
    name = _bare_name(qualified_name)
    if not name:
        return []
    folded = name.lower()
    # Exact and indexed first: the text search below caps an unordered scan of
    # partial matches, so a common name lost its exact hits past the cap (SC-6).
    rows = await graph_provider.get_documents_paginated(
        collection=_BLOCKS,
        limit=_NAME_CANDIDATE_LIMIT,
        filters={"orgId": org_id, "connectorId": connector_id, "nameFolded": folded},
        sort_field="_key",
        raise_on_error=True,
    )
    if not rows:
        # Blocks projected before nameFolded existed, until the backfill has run.
        rows = await graph_provider.search_nodes_by_field_terms(
            collection=_BLOCKS,
            field_name="name",
            terms=[name],
            filters={"orgId": org_id, "connectorId": connector_id},
            limit=_NAME_CANDIDATE_LIMIT,
            raise_on_error=True,
        )

    # The text search scores partial matches, and a walk anchored on a symbol
    # the caller did not name is worse than admitting the name did not resolve.
    matches = [
        row for row in rows or []
        if (row.get("name") or "").lower() == folded
        and row.get("orgId") == org_id
        and row.get("connectorId") == connector_id
    ]
    exact = [row for row in matches if row.get("qualifiedName") == qualified_name]
    resolved = exact or matches
    await attach_file_paths(graph_provider, org_id, resolved)
    return resolved


# Neighbours worth walking next (class of a method, heritage). CALLS rows are
# for read_code(lines=...); these are for another get_neighbour.
_CHAIN_RELATIONS = frozenset({*STRUCTURAL_RELATIONS, *HERITAGE_RELATIONS})


def _chain_targets(neighbors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str | None]] = set()
    for n in neighbors:
        if n.get("relation") not in _CHAIN_RELATIONS:
            continue
        key = (n.get("file_path"), n.get("qualified_name"))
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "file_path": n.get("file_path"),
            "qualified_name": n.get("qualified_name"),
            "relation": n.get("relation"),
        })
    return out


async def _name_candidates(
    graph_provider: IGraphDBProvider,
    anchors: list[dict[str, Any]],
    relations: list[str],
    qualified_name: str,
    direction: str,
    connector_id: str,
    org_id: str,
    *,
    include_tests: bool,
    access: RecordAccess,
) -> dict[str, Any]:
    """The addresses a bare name matched, instead of a walk of all of them.

    Walking every match and merging the rows would answer "who calls notify"
    with the union of several unrelated call sets — confidently wrong, and
    indistinguishable from the real answer. Returning the addresses keeps the
    caller's next call exact, and is what turns a name into something the
    "copy, do not compose" address rule allows.
    """
    if not include_tests:
        rids = {rid for a in anchors if (rid := a.get("recordId"))}
        roles = await get_record_roles(graph_provider, org_id, rids)
        kept = [a for a in anchors if roles.get(a.get("recordId")) != TEST_ROLE]
        anchors = kept or anchors

    degrees, degree_capped = await _degrees(graph_provider, anchors, relations, org_id, access)
    ranked = sorted(
        anchors,
        key=lambda b: (-degrees.get(_key_of(b), 0), b.get("filePath") or ""),
    )

    candidates = []
    for block in ranked[:_NEIGHBOUR_FANOUT]:
        ref = SymbolRef.from_block(block)
        ref["degree"] = degrees.get(_key_of(block), 0)
        candidates.append(ref)

    return {
        "symbol": None,
        "connector_id": connector_id,
        "direction": direction,
        "matched_name": _bare_name(qualified_name),
        "candidates": candidates,
        "truncated": len(ranked) > len(candidates),
        **({"degree_capped": True} if degree_capped else {}),
        "note": (
            f"'{qualified_name}' matches {len(ranked)} symbols in this repo, so "
            "nothing was walked — merging their edges would invent a caller set. "
            "Ranked by `degree` (edges touching the symbol), highest first. Call "
            "get_neighbour again with the `file_path` and `qualified_name` of the "
            "one you mean."
        ),
    }


async def get_neighbour_impl(
    *,
    graph_provider: IGraphDBProvider,
    connector_id: str,
    org_id: str,
    user_id: str,
    file_path: str | None = None,
    qualified_name: str | None = None,
    direction: str = "any",
    edge_types: list[str] | None = None,
    depth: int = 1,
    limit: int = DEFAULT_NEIGHBOR_LIMIT,
    offset: int = 0,
    include_tests: bool = False,
    repo: str | None = None,
) -> dict[str, Any]:
    if offset < 0:
        return {"error": "offset must be 0 or greater"}
    if direction not in ("inbound", "outbound", "any"):
        return {"error": "direction must be 'inbound', 'outbound' or 'any'"}

    # Rejected, not filtered. Dropping an unrecognized type and carrying on
    # would answer a typo (`['CALL']`) with every relation instead of none --
    # a narrower request silently becoming the broadest possible one.
    unknown = [e for e in (edge_types or []) if e not in CODE_RELATIONS]
    if unknown:
        return {"error": (
            f"unknown edge_types {unknown}. Valid: {', '.join(CODE_RELATIONS)}"
        )}
    relations = list(edge_types) if edge_types else list(CODE_RELATIONS)
    depth = max(1, min(int(depth or 1), MAX_NEIGHBOUR_DEPTH))

    if not file_path and not qualified_name:
        return {"error": (
            "Give `file_path`, `record_id`, or `qualified_name` — a walk needs "
            "somewhere to start."
        )}

    # One payload for a missing anchor and a denied one: an error naming the
    # path for the miss would say which of the two the caller hit.
    empty = {
        **({"symbol": None} if qualified_name else {"file_path": file_path}),
        "direction": direction, "neighbors": [],
    }
    by_name = not file_path
    access = RecordAccess(graph_provider, org_id, user_id)
    anchors = await (
        _resolve_anchors_by_name(graph_provider, org_id, qualified_name, connector_id)
        if by_name else
        _resolve_anchors(graph_provider, org_id, file_path, qualified_name, connector_id,
                         repo=repo, access=access)
    )
    if not anchors:
        return empty

    readable = await access.readable(a.get("recordId") for a in anchors)
    anchors = [a for a in anchors if a.get("recordId") in readable]
    if by_name and repo:
        names = await repo_names(graph_provider, org_id, (a.get("recordGroupId") for a in anchors))
        anchors = [a for a in anchors if _repo_matches(repo, a.get("recordGroupId"), names)]
    if not anchors:
        return empty
    if by_name and len(anchors) > 1:
        return await _name_candidates(
            graph_provider, anchors, relations, qualified_name, direction,
            connector_id, org_id, include_tests=include_tests, access=access,
        )
    fanout_capped = len(anchors) > _NEIGHBOUR_FANOUT
    anchors = anchors[:_NEIGHBOUR_FANOUT]

    anchor_names = {k: a.get("qualifiedName") for a in anchors
                    if (k := a.get("_key") or a.get("id"))}
    anchor_keys = list(anchor_names)
    anchor_order = {k: i for i, k in enumerate(anchor_keys)}
    # Every row is attributed to the anchor its chain started from, not to the
    # node one hop back, so a whole-file walk can say which symbol reaches what.
    origin_of = {k: k for k in anchor_keys}
    visited = set(anchor_keys)
    frontier = list(anchor_keys)
    rows: list[dict[str, Any]] = []
    seen_rows: set[tuple[str, str | None, str | None, str | None]] = set()
    for hop in range(1, depth + 1):
        if not frontier:
            break
        if len(frontier) > _NEIGHBOUR_FANOUT:
            fanout_capped = True
        expand = frontier[:_NEIGHBOUR_FANOUT]
        row_limit = min(_HOP_ROW_LIMIT, len(expand) * _ROWS_PER_ANCHOR)
        try:
            # One row past the limit is the only way to know the limit bit:
            # exactly `row_limit` rows is a complete answer, one more is not.
            batch = list(await graph_provider.get_neighbors_for_nodes_by_relationship_types(
                node_keys=expand,
                node_collection=_BLOCKS,
                relationship_types=relations,
                direction=direction,
                limit=row_limit + 1,
                raise_on_error=True,
            ) or [])
        except Exception as exc:
            logger.exception("Neighbour walk failed")
            raise RuntimeError("Failed to walk the code graph") from exc
        if len(batch) > row_limit:
            fanout_capped = True
            del batch[row_limit:]

        next_frontier: list[str] = []
        for row in batch:
            key = row.get("key")
            if not key:
                continue
            back = row.get("anchorKey")
            origin = origin_of.get(back, back)
            # A walk that comes back to where it started (A calls B calls A) or
            # reaches a node twice by different routes from one origin adds rows,
            # not facts. Two symbols of a file that both call it are two facts.
            seen_key = (key, row.get("relationshipType"), row.get("direction"), origin)
            if (hop > 1 and key in anchor_names) or seen_key in seen_rows:
                continue
            seen_rows.add(seen_key)
            row["hop"] = hop
            row["_origin"] = origin
            row["_via"] = back
            rows.append(row)
            if row.get("collection") == _BLOCKS and key not in visited:
                visited.add(key)
                origin_of[key] = origin
                next_frontier.append(key)
        # The next hop starts only from what the caller can read: rows reached
        # through a hidden file would show what that file calls (S-7).
        if hop < depth and next_frontier:
            readable_next = await _readable_keys(graph_provider, org_id, access, next_frontier)
            next_frontier = [k for k in next_frontier if k in readable_next]
        frontier = next_frontier

    block_keys = [r["key"] for r in rows if r.get("collection") == _BLOCKS and r.get("key")]
    if depth > 1:
        block_keys += [r["_via"] for r in rows if r.get("hop", 1) > 1 and r.get("_via")]
    blocks = await _readable_blocks(graph_provider, org_id, access, block_keys,
                                     connector_id=connector_id)

    hidden_tests: set[str] = set()
    if not include_tests:
        rid_set = {b.get("recordId") for b in blocks.values() if b.get("recordId")}
        roles = await get_record_roles(graph_provider, org_id, rid_set)
        hidden_tests = {k for k, b in blocks.items() if roles.get(b.get("recordId")) == TEST_ROLE}
        blocks = {k: b for k, b in blocks.items() if k not in hidden_tests}

    # Rank each row among its own anchor's, then take rank 0 from every anchor
    # before rank 1 from any: a whole-file walk otherwise spends the entire
    # budget on the first hub symbol and reports the rest of the file as having
    # no edges. With one anchor the ranks are already row order, so a
    # single-symbol walk still returns its nearest neighbours first.
    ranked: list[tuple[int, int, int, dict[str, Any], dict[str, Any]]] = []
    per_origin: dict[str, int] = {}
    # Ordered by what the rows say, not by the order the database returned
    # them in, so the same walk reads the same twice (A-8).
    present = [row for row in rows if row.get("key") in blocks]
    present.sort(key=lambda row: (
        row.get("hop", 1), row.get("relationshipType") or "",
        blocks[row["key"]].get("filePath") or "", blocks[row["key"]].get("qualifiedName") or "",
        row.get("direction") or "",
    ))
    for order, row in enumerate(present):
        block = blocks[row["key"]]
        origin = row.get("_origin")
        rank = per_origin.get(origin, 0)
        per_origin[origin] = rank + 1
        ranked.append((rank, row.get("hop", 1), order, row, block))
    ranked.sort(key=lambda item: item[:3])
    total = len(ranked)
    # Pages partition RANK order (so `offset` keeps the round-robin above),
    # while each page is displayed anchor-grouped below. Consecutive pages
    # therefore cover the full set, but not in the full result's display order.
    kept = ranked[offset:offset + limit]
    kept.sort(key=lambda item: (anchor_order.get(item[3].get("_origin"), 0), *item[:3]))

    neighbors: list[dict[str, Any]] = []
    for _rank, _hop, _order, row, block in kept:
        ref = SymbolRef.from_block(block)
        # The qualified name's own prefix already says it (A-8).
        ref.pop("kind", None)
        ref["relation"] = row.get("relationshipType")
        # Which way the edge points, from the node this row was reached from:
        # with direction="any", callers and callees otherwise read the same.
        ref["direction"] = row.get("direction") or (direction if direction != "any" else None)
        if not qualified_name:
            ref["from"] = anchor_names.get(row.get("_origin"))
        if depth > 1:
            ref["hop"] = row.get("hop")
            via = blocks.get(row.get("_via")) if row.get("hop", 1) > 1 else None
            if via is not None:
                ref["via"] = f"{via.get('filePath')}#{via.get('qualifiedName')}"
        if row.get("line") is not None:
            ref["line"] = row.get("line")
        if row.get("confidence"):
            ref["confidence"] = row.get("confidence")
        neighbors.append(ref)

    anchored = ({"symbol": SymbolRef.from_block(anchors[0])} if qualified_name
                else {"file_path": file_path, "symbols_walked": len(anchor_keys)})
    # One repo for every row is said once, not on each (A-8).
    repos = {n.get("repo") for n in neighbors}
    if len(repos) == 1 and None not in repos:
        for n in neighbors:
            n.pop("repo", None)
    # The arguments are not echoed back: the caller has them (A-8).
    result: dict[str, Any] = touched({
        **anchored,
        # Kept, unlike the other arguments: the next call needs it.
        "connector_id": connector_id,
        **({"repo": repos.pop()} if len(repos) == 1 and None not in repos else {}),
        **({"coverage": gap} if (gap := coverage_for(anchors)) else {}),
        "neighbors": neighbors,
        **({"offset": offset} if offset else {}),
        "total": total,
        **({"hidden_tests": len(hidden_tests),
            # A bare count reads as "no test callers worth listing"; say how to see them.
            "hint": (f"{len(hidden_tests)} neighbour(s) in test code are hidden; "
                     "pass include_tests=true to list them.")} if hidden_tests else {}),
        # Silence reads as absence: a model that thinks it saw every neighbour
        # will state a wrong conclusion confidently. A walk that hit its
        # traversal cap is truncated even when every fetched row fits the page.
        "truncated": total > offset + limit or fanout_capped,
    }, anchors)
    if fanout_capped:
        result["fanout_capped"] = True
    if total > offset + limit:
        shown_to = offset + len(neighbors)
        result["next"] = (
            f"Showing neighbours {offset + 1}-{shown_to} of {total}. Continue "
            f"with offset={shown_to} (same arguments) for the rest."
        )
    if fanout_capped:
        cap_note = (
            "The walk hit its traversal cap, so `total` is a lower bound and a "
            "neighbour missing here is not shown absent. Narrow with `edge_types` "
            "or `direction`, or walk from one `qualified_name` instead of the file."
        )
        result["next"] = f"{result['next']} {cap_note}" if "next" in result else cap_note
    chain = _chain_targets(neighbors)
    if chain:
        result["chain"] = chain
        result["note"] = (
            "Call get_neighbour on each `chain` entry next (omit edge_types) "
            "before read_code — that is how you keep traversing."
        )
    elif edge_types is not None and set(relations) <= {
        RecordRelations.CALLS.value,
    }:
        result["note"] = (
            "edge_types was CALLS-only, so METHOD/INHERITS were never returned. "
            "Omit edge_types on the next get_neighbour to learn the node's class "
            "and heritage, then walk those neighbours."
        )
    return result


# What a change to a symbol can break: its callers, importers, and the types
# and overrides built on it -- followed backwards.
IMPACT_RELATIONS = [
    RecordRelations.CALLS.value,
    RecordRelations.IMPORTS_FROM.value,
    RecordRelations.INHERITS.value,
    RecordRelations.IMPLEMENTS.value,
    RecordRelations.OVERRIDES.value,
]
DEFAULT_IMPACT_DEPTH = 2
_IMPACT_ROWS = 500
# Reached through these, a symbol builds on the target rather than calling it.
_BUILT_ON = frozenset({RecordRelations.INHERITS.value, RecordRelations.IMPLEMENTS.value,
                       RecordRelations.OVERRIDES.value})


async def impact_impl(
    *,
    graph_provider: IGraphDBProvider,
    connector_id: str,
    org_id: str,
    user_id: str,
    file_path: str,
    qualified_name: str | None = None,
    depth: int = DEFAULT_IMPACT_DEPTH,
    include_tests: bool = False,
    repo: str | None = None,
    edge_types: list[str] | None = None,
) -> dict[str, Any]:
    """What reaches a symbol, transitively, grouped by the file it is in (A-9).

    An inbound walk over IMPACT_RELATIONS (or the *edge_types* asked for, e.g.
    CALLS alone for callers): each file says which of its symbols are affected
    and how near they are, so a change's blast radius reads as a list of files
    to check rather than a flat list of edges.
    """
    unknown = sorted(set(edge_types or ()) - set(IMPACT_RELATIONS))
    if unknown:
        return {"error": f"unknown edge_types {unknown}. Valid: {', '.join(IMPACT_RELATIONS)}"}

    def walk_with(*, tests: bool) -> Awaitable[dict[str, Any]]:
        return get_neighbour_impl(
            graph_provider=graph_provider, connector_id=connector_id, org_id=org_id,
            user_id=user_id, file_path=file_path, qualified_name=qualified_name,
            direction="inbound", edge_types=edge_types or IMPACT_RELATIONS, depth=depth,
            limit=_IMPACT_ROWS, include_tests=tests, repo=repo,
        )

    walk = await walk_with(tests=include_tests)
    if "error" in walk or "neighbors" not in walk:
        return walk
    if qualified_name and walk.get("symbol") is None:
        # Read as "nothing depends on it", an unresolved address states a wrong conclusion.
        # Same words for a missing and an unreadable symbol, as the walk itself keeps them.
        return {"error": (
            f"No readable symbol {qualified_name!r} in {file_path!r}. Use the repo-relative "
            "path and name search_code returns, e.g. `method:Class.name`."
        )}
    out = {k: v for k, v in walk.items()
           if k not in ("neighbors", "chain", "note", "next", "hidden_tests", "hint")}
    files = _by_file(walk["neighbors"])
    out.update({"impacted_files": files, "impacted_symbols": sum(len(f["symbols"]) for f in files)})
    if not include_tests and walk.get("hidden_tests"):
        # The tests a change reaches are the ones to run: listed apart, not just counted,
        # so "what breaks" needs no second call.
        everything = await walk_with(tests=True)
        shown = {(n.get("file_path"), n.get("qualified_name")) for n in walk["neighbors"]}
        tests = [n for n in everything.get("neighbors", ())
                 if (n.get("file_path"), n.get("qualified_name")) not in shown]
        out["impacted_tests"] = _by_file(tests)
        out["truncated"] = out.get("truncated") or bool(everything.get("truncated"))
    return out


def _by_file(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_file: dict[str, dict[str, Any]] = {}
    for row in rows:
        path = row.get("file_path") or ""
        entry = by_file.setdefault(path, {"file_path": path, "symbols": [], "nearest_hop": 9})
        if row.get("qualified_name") not in entry["symbols"]:
            entry["symbols"].append(row.get("qualified_name"))
        if row.get("relation") in _BUILT_ON and row.get("qualified_name") not in entry.get("built_on", ()):
            # A subclass, implementation or override: affected, but not a caller.
            entry.setdefault("built_on", []).append(row.get("qualified_name"))
        entry["nearest_hop"] = min(entry["nearest_hop"], row.get("hop") or 1)
    return sorted(by_file.values(), key=lambda f: (f["nearest_hop"], f["file_path"]))
