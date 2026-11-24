"""get_neighbour: what a symbol or file reaches, and what reaches it."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import RecordRelations
from app.modules.code_graph.symbol_index import OVERRIDING_KINDS
from app.modules.parsers.code_parser.models import FILLER_KINDS

from .ops import (
    _BLOCKS,
    _RECORDS,
    CODE_RELATIONS,
    HERITAGE_RELATIONS,
    STRUCTURAL_RELATIONS,
    TEST_ROLE,
    RecordAccess,
    SymbolRef,
    _choose_records,
    _degrees,
    _key_of,
    _readable_block_records,
    _readable_blocks,
    _readable_kinds,
    _readable_neighbour_rows,
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

__all__ = ["DEFAULT_NEIGHBOR_LIMIT", "IMPACT_RELATIONS", "MAX_NEIGHBOUR_DEPTH", "MAX_IMPACT_DEPTH", "Walk", "get_neighbour_impl", "impact_impl", "walk_neighbours", "DEFAULT_IMPACT_DEPTH"]

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
# Raw edge rows scanned per hop while looking for readable ones.
_HOP_SCAN_LIMIT = 4 * _HOP_ROW_LIMIT
# How far up (or down) an override chain a lookup steps (C.m -> B.m -> A.m ...); real hierarchies are shallow.
_OVERRIDE_LEVELS = 8
# Only these have OVERRIDES edges (symbol_index's method index). A kind not known (None) is looked up anyway:
# skipping it could hide an interface's callers.
_MAY_OVERRIDE = OVERRIDING_KINDS | {None}
# Implementations named per base or interface method an outbound walk reaches; the rest are counted.
_IMPLEMENTATIONS_PER_CALLEE = 8
# Files listed per imported symbol, nearest first; the rest are counted (a hub file can have hundreds).
_IMPORTERS_PER_SYMBOL = 50


async def _readable_rows(
    graph_provider: IGraphDBProvider, org_id: str, access: RecordAccess, keys: list[str], relation: str,
    direction: str, *, node_collection: str = _BLOCKS,
) -> tuple[list[dict[str, Any]], bool]:
    """Every *relation* row from *keys* whose far end the caller can read, test files included:
    ``(rows, incomplete)``, incomplete when the scan budget ran out first."""
    rows, _over, incomplete = await _readable_neighbour_rows(
        graph_provider, org_id, access, keys, [relation], direction,
        page=_HOP_ROW_LIMIT, budget=_HOP_SCAN_LIMIT, node_collection=node_collection,
    )
    return rows, incomplete


async def _override_closure(
    graph_provider: IGraphDBProvider, org_id: str, access: RecordAccess, keys: list[str], direction: str,
) -> tuple[dict[str, str], bool]:
    """The methods OVERRIDES reaches from the blocks in *keys*, transitively, each mapped to the block of *keys*
    it was reached from, nearest level first: ``outbound`` the bases they override or implement, ``inbound`` the
    methods overriding them. One query per level, up to _OVERRIDE_LEVELS, cycle-safe. Returns
    ``(found, incomplete)``.

    Only methods the caller may read are returned, and a hidden one is not followed further (fail closed):
    what lies past it would be reached through a file the caller cannot see.
    """
    found: dict[str, str] = {}
    level = {k: k for k in keys}
    seen = set(keys)
    incomplete = False
    for _ in range(_OVERRIDE_LEVELS):
        if not level:
            break
        rows, spent = await _readable_rows(graph_provider, org_id, access, sorted(level),
                                           RecordRelations.OVERRIDES.value, direction)
        incomplete = incomplete or spent
        reached: dict[str, str] = {}
        for row in rows:
            key, back = row.get("key"), row.get("anchorKey")
            if row.get("collection") == _BLOCKS and key and key not in seen and back in level:
                seen.add(key)
                reached[key] = level[back]
        found.update(reached)
        level = reached
    return found, incomplete


async def _implementations(
    graph_provider: IGraphDBProvider, org_id: str, access: RecordAccess, callee_keys: list[str],
) -> tuple[dict[str, list[tuple[str, bool]]], int, bool]:
    """``({callee: [(implementation, instantiated), ...]}, omitted, incomplete)``: per callee, the methods
    overriding it (transitively, readable only), best first and at most _IMPLEMENTATIONS_PER_CALLEE, and how
    many were left out in all.

    Ranked by rapid type analysis, then degree, then path: an implementation whose class is constructed in the
    repo (a readable CALLS into the class: constructor calls land on class blocks) can run, one never
    constructed likely does not, and a busier one is the likelier answer.
    """
    closure, incomplete = await _override_closure(graph_provider, org_id, access, callee_keys, "inbound")
    if not closure:
        return {}, 0, incomplete
    blocks = await _readable_blocks(graph_provider, org_id, access, list(closure))
    owners = {k: owner for k, b in blocks.items() if (owner := b.get("parentBlockId"))}
    constructed: set[str] = set()
    if owners:
        rows, spent = await _readable_rows(graph_provider, org_id, access, sorted(set(owners.values())),
                                           RecordRelations.CALLS.value, "inbound")
        incomplete = incomplete or spent
        constructed = {r.get("anchorKey") for r in rows}
    degrees, _capped = await _degrees(graph_provider, list(blocks.values()), [RecordRelations.CALLS.value],
                                      org_id, access)
    by_callee: dict[str, list[str]] = {}
    for impl, callee in closure.items():
        if impl in blocks:
            by_callee.setdefault(callee, []).append(impl)
    out: dict[str, list[tuple[str, bool]]] = {}
    omitted = 0
    for callee, impls in by_callee.items():
        impls.sort(key=lambda k: (owners.get(k) not in constructed, -degrees.get(k, 0),
                                  blocks[k].get("filePath") or "", blocks[k].get("qualifiedName") or ""))
        out[callee] = [(k, owners.get(k) in constructed) for k in impls[:_IMPLEMENTATIONS_PER_CALLEE]]
        omitted += max(0, len(impls) - _IMPLEMENTATIONS_PER_CALLEE)
    return out, omitted, incomplete


def _shared_dirs(a: str, b: str) -> int:
    return len([1 for x, y in zip(a.split("/")[:-1], b.split("/")[:-1], strict=False) if x == y])


async def _importers(
    graph_provider: IGraphDBProvider, org_id: str, access: RecordAccess,
    imported: dict[str, tuple[int, dict[str, Any]]], file_records: dict[str, str], *, include_tests: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, bool]:
    """The files that import what impact reports: ``(rows, test rows, omitted, incomplete)``.

    *imported*: reported block key -> (its hop, its block); *file_records*: record id -> path, for a whole-file
    anchor. An import fact starts at the importing file's ``imports`` block, or at its record (a pinned fact),
    so each inbound row is read back to its record. Only readable files are listed, the symbol's own file never,
    and at most _IMPORTERS_PER_SYMBOL per imported symbol (nearest path first); the rest are counted.
    """
    found: list[tuple[str, str, int, str, str]] = []  # (importer row collection, key, hop, imported name, path)
    incomplete = False
    if imported:
        rows, incomplete = await _readable_rows(graph_provider, org_id, access, sorted(imported),
                                                RecordRelations.IMPORTS.value, "inbound")
        for row in rows:
            hop, block = imported.get(row.get("anchorKey"), (0, {}))
            found.append((row.get("collection"), row.get("key"), hop + 1, block.get("qualifiedName"),
                          block.get("filePath") or ""))
    if file_records:
        rows, spent = await _readable_rows(graph_provider, org_id, access, sorted(file_records),
                                           RecordRelations.IMPORTS_FROM.value, "inbound", node_collection=_RECORDS)
        incomplete = incomplete or spent
        for row in rows:
            path = file_records.get(row.get("anchorKey")) or ""
            found.append((row.get("collection"), row.get("key"), 1, path, path))

    via_blocks = [key for coll, key, *_ in found if coll == _BLOCKS and key]
    owner = await _readable_block_records(graph_provider, org_id, access, via_blocks) if via_blocks else {}
    own = {b.get("recordId") for _hop, b in imported.values()} | set(file_records)
    candidates = [(owner.get(key) if coll == _BLOCKS else key, hop, name, path)
                  for coll, key, hop, name, path in found]
    candidates = [c for c in candidates if c[0] and c[0] not in own]
    if not candidates:
        return [], [], 0, incomplete
    rows = [{"recordId": rid} for rid in sorted({c[0] for c in candidates})]
    await attach_file_paths(graph_provider, org_id, rows)
    paths = {r["recordId"]: r for r in rows if r.get("filePath")}
    roles = {} if include_tests else await get_record_roles(graph_provider, org_id, set(paths))

    by_symbol: dict[tuple[str, str], list[tuple[str, int]]] = {}
    for rid, hop, name, target_path in candidates:
        if rid in paths:
            by_symbol.setdefault((name, target_path), []).append((rid, hop))
    kept: dict[str, dict[str, Any]] = {}
    omitted = 0
    for (name, target_path), hits in sorted(by_symbol.items()):
        nearest: dict[str, int] = {}
        for rid, hop in hits:
            nearest[rid] = min(hop, nearest.get(rid, hop))
        ranked = sorted(nearest.items(), key=lambda kv: (-_shared_dirs(paths[kv[0]]["filePath"], target_path),
                                                         paths[kv[0]]["filePath"]))
        omitted += max(0, len(ranked) - _IMPORTERS_PER_SYMBOL)
        for rid, hop in ranked[:_IMPORTERS_PER_SYMBOL]:
            entry = kept.setdefault(rid, {"file_path": paths[rid]["filePath"], "imports": [], "hop": hop,
                                          **({"repo": paths[rid]["repo"]} if paths[rid].get("repo") else {})})
            entry["hop"] = min(entry["hop"], hop)
            if name and name not in entry["imports"]:
                entry["imports"].append(name)
    listed = sorted(kept.items(), key=lambda kv: kv[1]["file_path"])
    tests = [e for rid, e in listed if roles.get(rid) == TEST_ROLE]
    return [e for rid, e in listed if roles.get(rid) != TEST_ROLE], tests, omitted, incomplete


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
) -> tuple[list[dict[str, Any]], str]:
    """The symbols a walk starts from: one named symbol, or the whole file,
    and the indexed path they came from (a checkout prefix the index lacks is dropped).

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
        return ([anchor] if anchor else []), file_path

    record_ids, matched_path = await _records_for_path(
        graph_provider, org_id, file_path,
        choose=lambda ids: _choose_records(
            graph_provider, org_id, ids, connector_id,
            file_path=file_path, repo=repo, access=access,
        ),
    )
    if not record_ids:
        return [], file_path
    rows = await graph_provider.get_nodes_by_field_in(
        collection=_BLOCKS, field_name="recordId", field_values=record_ids, raise_on_error=True
    )
    mine = [row for row in (rows or [])
            if row.get("orgId") == org_id and row.get("connectorId") == connector_id]
    mine.sort(key=lambda row: (
        (row.get("kind") or "").lower() in FILLER_KINDS, row.get("startLine") or 0,
        str(row.get("_key") or row.get("id") or ""),  # a tie keeps its key order, so the cut below never depends on the store's
    ))
    # One past the cap, so the caller can tell "exactly 200" from "more".
    return mine[:_NEIGHBOUR_FANOUT + 1], matched_path


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


@dataclass
class Walk:
    """What a neighbour walk found, before anyone words it.

    ``rows`` are edge rows in walk order whose far block is in ``blocks``: the
    caller can read it, it is in the connector, and it is not test code unless
    asked for. Each carries ``key`` (block key), ``hop``, ``_origin`` (the anchor
    the chain started from) and ``_via`` (the node one hop back). A row reached
    through an overridden method carries ``_stone`` (its key, a block of
    ``stone_blocks``); an implementation of a callee carries ``_impl_of`` (the
    callee) and ``_instantiated``.
    """
    anchors: list[dict[str, Any]]
    rows: list[dict[str, Any]]
    blocks: dict[str, dict[str, Any]]
    hidden_tests: set[str] = field(default_factory=set)
    fanout_capped: bool = False
    incomplete: bool = False
    stone_blocks: dict[str, dict[str, Any]] = field(default_factory=dict)
    implementations_omitted: int = 0


async def walk_neighbours(
    graph_provider: IGraphDBProvider,
    org_id: str,
    user_id: str,
    anchors: list[dict[str, Any]],
    *,
    relations: list[str],
    direction: str,
    connector_id: str,
    depth: int = 1,
    include_tests: bool = False,
    row_limit: int | None = None,
    access: RecordAccess | None = None,
    override_bases: bool = False,
    implementations: bool = False,
) -> Walk:
    """Walk ``depth`` hops from ``anchors`` over ``relations``, reading only what the caller may.

    ``anchors`` are block documents the caller already resolved and may read.
    Pass the caller's ``access`` to reuse its verdicts; a second check of the
    same record then costs no query. ``row_limit`` stops each hop's read at that
    many readable rows (one more is read to tell "exactly" from "more");
    the default is the per-hop cap the tools use. ``override_bases`` (impact)
    also walks the methods the frontier overrides at each hop, as stepping
    stones: walked for their callers, never reported. ``implementations``
    (get_neighbour) adds, beside each outbound CALLS callee, the methods that
    implement it. Both are off by default, so a caller paging the stored edges
    (the graph read API) gets exactly those.
    """
    access = access or RecordAccess(graph_provider, org_id, user_id)
    fanout_capped = len(anchors) > _NEIGHBOUR_FANOUT
    incomplete = False
    anchors = anchors[:_NEIGHBOUR_FANOUT]

    anchor_names = {k: a.get("qualifiedName") for a in anchors
                    if (k := a.get("_key") or a.get("id"))}
    anchor_keys = list(anchor_names)
    # Every row is attributed to the anchor its chain started from, not to the
    # node one hop back, so a whole-file walk can say which symbol reaches what.
    origin_of = {k: k for k in anchor_keys}
    visited = set(anchor_keys)
    frontier = list(anchor_keys)
    rows: list[dict[str, Any]] = []
    seen_rows: set[tuple[str, str | None, str | None, str | None]] = set()
    # Stepping stones: base key -> the frontier block that overrides it. A call typed to an interface resolves
    # to the declared method, so its callers are walked as if the frontier block had them, at the same hop.
    stones: dict[str, str] = {}
    implementations_omitted = 0
    kind_of: dict[str, str | None] = {k: a.get("kind") for a in anchors if (k := a.get("_key") or a.get("id"))}
    for hop in range(1, depth + 1):
        if not frontier:
            break
        overriding = [k for k in frontier if kind_of.get(k) in _MAY_OVERRIDE]
        if override_bases and overriding:
            bases, spent = await _override_closure(graph_provider, org_id, access, overriding, "outbound")
            incomplete = incomplete or spent
            for base, child in bases.items():
                if base not in visited:
                    visited.add(base)
                    origin_of[base] = origin_of.get(child, child)
                    stones[base] = child
                    frontier.append(base)
        if len(frontier) > _NEIGHBOUR_FANOUT:
            fanout_capped = True
        expand = frontier[:_NEIGHBOUR_FANOUT]
        if row_limit is None:
            hop_rows = min(_HOP_ROW_LIMIT, len(expand) * _ROWS_PER_ANCHOR)
        else:
            hop_rows = row_limit
        # One readable row past the limit is the only way to know the limit bit:
        # exactly `hop_rows` rows is a complete answer, one more is not.
        batch, over, spent = await _readable_neighbour_rows(
            graph_provider, org_id, access, expand, relations, direction,
            page=hop_rows + 1, budget=_HOP_SCAN_LIMIT, want=hop_rows,
            connector_id=connector_id, include_tests=include_tests,
            accept=lambda r: r.get("collection") == _BLOCKS,
        )
        fanout_capped = fanout_capped or over
        incomplete = incomplete or spent

        next_frontier: list[str] = []
        for row in batch:
            key = row.get("key")
            if not key:
                continue
            back = row.get("anchorKey")
            if back in stones and (row.get("relationshipType") in _BUILT_ON or key in anchor_names):
                # A base's other implementations are siblings, not dependents; the anchor is not its own caller.
                continue
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
            if back in stones:
                row["_stone"] = back
            rows.append(row)
            if row.get("collection") == _BLOCKS and key not in visited:
                visited.add(key)
                origin_of[key] = origin
                next_frontier.append(key)
        # A call to a base or interface method runs one of its implementations: name them beside it.
        callee_rows = [r for r in rows if implementations and r.get("hop") == hop and r.get("direction") == "outbound"
                       and r.get("relationshipType") == RecordRelations.CALLS.value
                       and r.get("collection") == _BLOCKS and "_impl_of" not in r]
        if callee_rows:
            found, omitted, spent = await _implementations(
                graph_provider, org_id, access, list(dict.fromkeys(r["key"] for r in callee_rows)))
            implementations_omitted += omitted
            incomplete = incomplete or spent
            for callee in callee_rows:
                for impl, instantiated in found.get(callee["key"], ()):
                    seen_key = (impl, callee["relationshipType"], "outbound", callee["_origin"])
                    if impl in anchor_names or seen_key in seen_rows:
                        continue
                    seen_rows.add(seen_key)
                    rows.append({"key": impl, "collection": _BLOCKS, "relationshipType": callee["relationshipType"],
                                 "direction": "outbound", "anchorKey": callee.get("anchorKey"), "hop": hop,
                                 "_origin": callee["_origin"], "_via": callee["_via"],
                                 "_impl_of": callee["key"], "_instantiated": instantiated})
                    if impl not in visited:  # what it calls is the next hop, as for the callee
                        visited.add(impl)
                        origin_of[impl] = callee["_origin"]
                        next_frontier.append(impl)
        # The next hop starts only from what the caller can read: rows reached
        # through a hidden file would show what that file calls (S-7).
        if hop < depth and next_frontier:
            readable_next = await _readable_kinds(graph_provider, org_id, access, next_frontier)
            kind_of.update(readable_next)
            next_frontier = [k for k in next_frontier if k in readable_next]
        frontier = next_frontier

    block_keys = [r["key"] for r in rows if r.get("collection") == _BLOCKS and r.get("key")]
    if depth > 1:
        block_keys += [r["_via"] for r in rows if r.get("hop", 1) > 1 and r.get("_via")]
    blocks = await _readable_blocks(graph_provider, org_id, access, block_keys,
                                     connector_id=connector_id)
    # Named in `via_override`, never listed as rows: fetched apart, so they count in nothing.
    stone_blocks = await _readable_blocks(graph_provider, org_id, access, list(stones),
                                          connector_id=connector_id) if stones else {}

    hidden_tests: set[str] = set()
    if not include_tests:
        rid_set = {b.get("recordId") for b in blocks.values() if b.get("recordId")}
        roles = await get_record_roles(graph_provider, org_id, rid_set)
        hidden_tests = {k for k, b in blocks.items() if roles.get(b.get("recordId")) == TEST_ROLE}
        blocks = {k: b for k, b in blocks.items() if k not in hidden_tests}

    present = [row for row in rows if row.get("key") in blocks]
    return Walk(anchors, present, blocks, hidden_tests, fanout_capped, incomplete, stone_blocks,
                implementations_omitted)


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
    max_depth: int = MAX_NEIGHBOUR_DEPTH,
    override_bases: bool = False,
    with_importers: bool = False,
    walk: bool = True,
) -> dict[str, Any]:
    """Callers and callees of one symbol (or every symbol of a file), as the code-graph neighbours tool answers them.

    The walk itself is ``walk_neighbours``; this shapes its rows into the tool's answer (nodes, edges and counts).
    Impact only: *override_bases* walks through the methods the frontier overrides; *with_importers* adds the files
    importing what the walk reports, under ``_importers``; with *walk* False only the anchor is resolved (impact's
    ``edge_types=["IMPORTS"]``: importers, no walk).
    """
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
    asked = int(depth or 1)
    depth = max(1, min(asked, max_depth))

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
    if by_name:
        anchors = await _resolve_anchors_by_name(graph_provider, org_id, qualified_name, connector_id)
    else:
        anchors, file_path = await _resolve_anchors(
            graph_provider, org_id, file_path, qualified_name, connector_id, repo=repo, access=access,
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
    found = await walk_neighbours(
        graph_provider, org_id, user_id, anchors, relations=relations, direction=direction,
        depth=depth if walk else 0, connector_id=connector_id, include_tests=include_tests, access=access,
        override_bases=override_bases, implementations=True,
    )
    anchors, blocks, hidden_tests = found.anchors, found.blocks, found.hidden_tests
    stone_blocks = found.stone_blocks
    fanout_capped, incomplete = found.fanout_capped, found.incomplete
    anchor_names = {k: a.get("qualifiedName") for a in anchors
                    if (k := a.get("_key") or a.get("id"))}
    anchor_keys = list(anchor_names)
    anchor_order = {k: i for i, k in enumerate(anchor_keys)}

    # Rank each row among its own anchor's, then take rank 0 from every anchor
    # before rank 1 from any: a whole-file walk otherwise spends the entire
    # budget on the first hub symbol and reports the rest of the file as having
    # no edges. With one anchor the ranks are already row order, so a
    # single-symbol walk still returns its nearest neighbours first.
    ranked: list[tuple[int, int, int, dict[str, Any], dict[str, Any]]] = []
    per_origin: dict[str, int] = {}
    # Ordered by what the rows say, not by the order the database returned
    # them in, so the same walk reads the same twice (A-8).
    present = sorted(found.rows, key=lambda row: (
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
        stone = stone_blocks.get(row.get("_stone")) if row.get("_stone") else None
        if stone is not None:
            ref["via_override"] = f"{stone.get('filePath')}#{stone.get('qualifiedName')}"
        if row.get("_impl_of"):
            # Not an edge of its own: the callee's implementation, so `of` names the method the call names. Past
            # hop 1 `via` keeps the hop path every row there has; `of` alone marks the implementation.
            ref.setdefault("via", "implementation")
            if (of := blocks.get(row["_impl_of"])) is not None:
                ref["of"] = f"{of.get('filePath')}#{of.get('qualifiedName')}"
            ref["instantiated"] = row["_instantiated"]
        if row.get("line") is not None:
            ref["line"] = row.get("line")
        if row.get("confidence"):
            ref["confidence"] = row.get("confidence")
        neighbors.append(ref)

    importers = None
    if with_importers:
        # The anchor and what the walk reports; a stepping stone is never a row, so it brings no importers.
        reported = {k: (0, a) for a in anchors if (k := a.get("_key") or a.get("id"))}
        for row in present:
            # An importer is one hop past what it imports: from the last hop it would be past `depth`.
            if row.get("hop", 1) < depth:
                reported.setdefault(row["key"], (row.get("hop", 1), blocks[row["key"]]))
        whole_file = {} if qualified_name else {a["recordId"]: file_path for a in anchors if a.get("recordId")}
        importers = await _importers(graph_provider, org_id, access, reported, whole_file,
                                     include_tests=include_tests)
        incomplete = incomplete or importers[3]

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
        # A deeper walk cut short silently reads as "nothing further up".
        **({"depth_capped": f"depth {asked} asked, {depth} walked (the most this tool follows)"}
           if asked > depth else {}),
        **({"hidden_tests": len(hidden_tests),
            # A bare count reads as "no test callers worth listing"; say how to see them.
            "hint": (f"{len(hidden_tests)} neighbour(s) in test code are hidden; "
                     "pass include_tests=true to list them.")} if hidden_tests else {}),
        # Silence reads as absence: a model that thinks it saw every neighbour
        # will state a wrong conclusion confidently. A walk that hit its
        # traversal cap is truncated even when every fetched row fits the page.
        "truncated": total > offset + limit or fanout_capped or incomplete,
    }, anchors)
    if found.implementations_omitted:
        result["implementations_omitted"] = found.implementations_omitted
    if fanout_capped:
        result["fanout_capped"] = True
    if incomplete:
        result["incomplete"] = True
    if importers is not None:
        result["_importers"] = importers[:3]  # impact_impl takes it off; never part of an answer
    if total > offset + limit:
        shown_to = offset + len(neighbors)
        result["next"] = (
            f"Showing neighbours {offset + 1}-{shown_to} of {total}. Continue "
            f"with offset={shown_to} (same arguments) for the rest."
        )
    if incomplete:
        cap_note = (
            "The walk stopped scanning a node's edges before the end, so `total` is a lower "
            "bound. Narrow with `edge_types` or `direction`."
        )
        result["next"] = f"{result['next']} {cap_note}" if "next" in result else cap_note
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
    # Whoever registers a handler or passes a callback runs it, later.
    RecordRelations.INDIRECT_CALL.value,
    RecordRelations.IMPORTS_FROM.value,
    RecordRelations.INHERITS.value,
    RecordRelations.IMPLEMENTS.value,
    RecordRelations.OVERRIDES.value,
]
DEFAULT_IMPACT_DEPTH = 2
# An inbound call walk stays narrow enough for five levels; get_neighbour's any-edge walk does not.
MAX_IMPACT_DEPTH = 5
_IMPACT_ROWS = 500
# In impact's edge_types: "list the files importing what is reached" (never a relation the walk follows).
_IMPORTER_STEP = RecordRelations.IMPORTS.value
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
    edge_types = edge_types or None  # an empty list is "not given" (all relations), as it always was
    valid = [*IMPACT_RELATIONS, _IMPORTER_STEP]
    unknown = sorted(set(edge_types or ()) - set(valid))
    if unknown:
        return {"error": f"unknown edge_types {unknown}. Valid: {', '.join(valid)}"}
    # IMPORTS is the importer step, not an edge the walk follows: an import fact starts at a file, not a symbol.
    relations = [r for r in (edge_types or IMPACT_RELATIONS) if r != _IMPORTER_STEP]
    with_importers = edge_types is None or bool({_IMPORTER_STEP, RecordRelations.IMPORTS_FROM.value} & set(edge_types))

    def walk_with(*, tests: bool, importers: bool = False) -> Awaitable[dict[str, Any]]:
        return get_neighbour_impl(
            graph_provider=graph_provider, connector_id=connector_id, org_id=org_id,
            user_id=user_id, file_path=file_path, qualified_name=qualified_name,
            direction="inbound", edge_types=relations or IMPACT_RELATIONS, depth=depth,
            limit=_IMPACT_ROWS, include_tests=tests, repo=repo, max_depth=MAX_IMPACT_DEPTH,
            override_bases=True, with_importers=importers, walk=bool(relations),
        )

    walk = await walk_with(tests=include_tests, importers=with_importers)
    importers, test_importers, omitted = walk.pop("_importers", None) or ([], [], 0)
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
    out.update({"impacted_files": _with_importers(files, importers),
                "impacted_symbols": sum(len(f["symbols"]) for f in files)})
    if not include_tests and walk.get("hidden_tests"):
        # The tests a change reaches are the ones to run: listed apart, not just counted,
        # so "what breaks" needs no second call.
        everything = await walk_with(tests=True)
        shown = {(n.get("file_path"), n.get("qualified_name")) for n in walk["neighbors"]}
        tests = [n for n in everything.get("neighbors", ())
                 if (n.get("file_path"), n.get("qualified_name")) not in shown]
        out["impacted_tests"] = _by_file(tests)
        out["truncated"] = out.get("truncated") or bool(everything.get("truncated"))
    if test_importers:
        out["impacted_tests"] = _with_importers(out.get("impacted_tests") or [], test_importers)
    if omitted:
        out["importers_omitted"] = omitted
        out["truncated"] = True
        out["note"] = (f"{omitted} more file(s) import a listed symbol; at most {_IMPORTERS_PER_SYMBOL} importing "
                       "files are listed per symbol, nearest first. Run impact on that symbol alone, or "
                       "search_code for its name, to see the rest.")
    return out


def _with_importers(files: list[dict[str, Any]], importers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """*files* plus the importing files not already listed, after the callers of the same hop.

    A file that also calls is listed once, as a caller: that is the nearer and stronger dependency.
    """
    listed = {f["file_path"] for f in files}
    extra = [{"file_path": i["file_path"], "symbols": [], "imports": i["imports"], "nearest_hop": i["hop"],
              "via": "imports", **({"repo": i["repo"]} if "repo" in i else {})}
             for i in importers if i["file_path"] not in listed]
    return sorted([*files, *extra], key=lambda f: (f["nearest_hop"], f.get("via") == "imports", f["file_path"]))


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
        if row.get("via_override"):
            # Reached through the declared method it calls: says why a caller of the interface is listed.
            entry.setdefault("via_override", {}).setdefault(row.get("qualified_name"), row["via_override"])
        entry["nearest_hop"] = min(entry["nearest_hop"], row.get("hop") or 1)
    return sorted(by_file.values(), key=lambda f: (f["nearest_hop"], f["file_path"]))
