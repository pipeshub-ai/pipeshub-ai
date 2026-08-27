"""The generic code-graph query primitive.

One call shape, varied by arguments, that an agent invokes repeatedly:

    select="analyze_game", relations=["CALLS"], direction="inbound"   -> callers
    select="frontend/**", relations=["IMPORTS_FROM"],
        group_by="directory", depth=1                                 -> module graph
    select="src/api/client.py", depth=0                               -> what a file holds
    select="chess analysis"                                           -> cold start

Kept separate from ``ops.py`` so the narrow tools stay readable; both share
``ops``' access-control helpers.
"""
from __future__ import annotations

import fnmatch
import logging
import posixpath
import re
from collections import Counter, defaultdict
from typing import Any

from app.config.constants.arangodb import CollectionNames, RecordRelations
from app.modules.parsers.code_parser.models import FILLER_KINDS

from .ops import SymbolRef, _readable_blocks, _user_can_read, attach_file_paths

logger = logging.getLogger(__name__)

__all__ = [
    "CONNECTOR_ID_REQUIRED",
    "DEFAULT_QUERY_LIMIT",
    "GROUP_BY_CHOICES",
    "MAX_QUERY_DEPTH",
    "QUERY_CODE_GRAPH_TOOL_NAME",
    "query_code_graph_impl",
]

# The granted spelling, for prompt text that has to name the tool. Mirrors
# `FETCH_RECORD_TOOL_NAME`; a prompt that names a tool the caller cannot call is
# worse than one that stays silent.
QUERY_CODE_GRAPH_TOOL_NAME = "codegraph__query_code_graph"

_BLOCKS = CollectionNames.BLOCKS.value
_RECORDS = CollectionNames.RECORDS.value
_CODE_FILES = CollectionNames.CODE_FILES.value

CONNECTOR_ID_REQUIRED = (
    "connector_id is required. Run a knowledge search first and copy the "
    "`Connector ID` shown on any record from the repository you mean — the code "
    "graph spans every indexed repo, so it has to be told which one."
)

DEFAULT_QUERY_LIMIT = 50
MAX_QUERY_LIMIT = 200
MAX_QUERY_DEPTH = 3
GROUP_BY_CHOICES = ("none", "file", "directory")

ALL_RELATIONS = [r.value for r in RecordRelations]

# Filler spans exist so the blocks of a file tile it exactly. They are real
# content and stay reachable by an explicit path selector, but they would swamp a
# ranked free-text result -- every file has an imports block. Sourced from the
# parser so a newly added filler kind is excluded here without a second edit.
_NOISE_KINDS = FILLER_KINDS | {"file_summary"}

# How many blocks a free-text selector may consider before ranking, and how
# many FILES a path selector may match.
_SELECT_SCAN_LIMIT = 400
# Files whose blocks a single path selector will actually load.
_PATH_FANOUT_FILES = 60
# Files a directory listing may scan before it reports itself truncated.
_LIST_SCAN_LIMIT = 2000
_EXPAND_FANOUT = 400

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
# Rollup depth when the selector gives nothing to measure from (free text, a
# qualified name): `backend/python/app` and `frontend/app/(main)` are the level at
# which this repo reads as modules.
_CODE_EXTENSIONS = (".py", ".ts", ".tsx", ".js", ".jsx")
_MODULE_DEPTH = 3
_MAX_MODULE_DEPTH = 8


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "") if len(t) > 1]


def _looks_like_path(select: str) -> bool:
    return "/" in select or "*" in select or select.endswith(_CODE_EXTENSIONS)


def _looks_like_directory(select: str) -> bool:
    """A path with no glob and no file extension -- list its children.

    `app/agents/` and `app/agents` both mean "what is in here". Without this
    they take the prefix branch and return a jumble of blocks from whichever
    files scanned first, which is never what the caller wanted.
    """
    if "*" in select or "?" in select:
        return False
    return "/" in select and not select.endswith(_CODE_EXTENSIONS)


def _module_of(file_path: str | None, depth: int = _MODULE_DEPTH) -> str:
    if not file_path:
        return ""
    parts = [p for p in file_path.split("/") if p]
    if len(parts) <= 1:
        return parts[0] if parts else ""
    return "/".join(parts[:depth]) if len(parts) > depth else posixpath.dirname(file_path)


def _module_depth_for(prefix: str) -> int:
    """One level below whatever the selector already narrowed to.

    A fixed depth cannot serve both `frontend/**` and `backend/python/app/**`:
    at depth 3 the first splits into real modules and the second collapses into
    a single 1,170-file bucket. Measuring from the selector makes the tool
    drill down instead -- `**` gives top-level directories, and re-asking with
    a longer prefix opens whichever one matters.
    """
    segments = len([p for p in prefix.split("/") if p and "*" not in p])
    return max(1, min(segments + 1, _MAX_MODULE_DEPTH))


# ---------------------------------------------------------------------------
# Selector resolution
# ---------------------------------------------------------------------------

async def _select_by_path(
    graph_provider: Any, org_id: str, pattern: str, connector_id: str
) -> list[dict]:
    """Blocks of the files whose path matches a glob (or a literal prefix).

    Matched against ``codeFiles``, which holds one row per file, rather than
    against blocks, which hold ~30 copies of the same path. The scan limit
    therefore counts files, not symbols.

    Returns ``(rows, scan_capped)``. Both caps bite silently otherwise: the
    caller counts what it got and would report a clean ``truncated: false``
    for a prefix that was cut short.
    """
    # Anchor the DB-side filter on the pattern's literal prefix so the index does
    # the work; the glob itself is applied in Python.
    prefix = pattern.split("*", 1)[0]
    files = await graph_provider.get_nodes_by_field_prefix(
        collection=_CODE_FILES,
        field_name="filePath",
        prefix=prefix,
        filters={"orgId": org_id},
        limit=_SELECT_SCAN_LIMIT,
    )
    paths: dict[str, str] = {}
    for raw in files or []:
        row = _unwrap(raw)
        key = row.get("_key") or row.get("id")
        path = row.get("filePath")
        if not key or not path:
            continue
        if ("*" in pattern or "?" in pattern) and not fnmatch.fnmatch(path, pattern):
            continue
        paths[key] = path
    if not paths:
        return [], False

    # Bound the fan-out. Files are cheap to list, blocks are not: `backend/**`
    # matches thousands of files at ~30 blocks each, and an unbounded IN would
    # pull the whole repo into memory to answer one call.
    capped = sorted(paths)[:_PATH_FANOUT_FILES]
    scan_capped = len(files or []) >= _SELECT_SCAN_LIMIT or len(capped) < len(paths)
    rows = await graph_provider.get_nodes_by_field_in(
        collection=_BLOCKS, field_name="recordId", field_values=capped
    )
    out = []
    for raw in rows or []:
        row = _unwrap(raw)
        # codeFiles carries no connectorId, so the repo scope is re-applied here.
        if row.get("orgId") != org_id or row.get("connectorId") != connector_id:
            continue
        row["filePath"] = paths.get(row.get("recordId"))
        out.append(row)
    return out, scan_capped


async def _list_children(
    graph_provider: Any, org_id: str, prefix: str, connector_id: str, limit: int
) -> dict[str, Any]:
    """Immediate files and subdirectories under a directory prefix.

    Reads ``codeFiles`` rather than deriving from blocks: the rollup in
    `_group_rows` is built from edge rows, so a file nothing imports would be
    invisible there. An inventory has to come from the file list itself.
    """
    prefix = prefix.rstrip("/") + "/" if prefix.strip("/") else ""
    rows = await graph_provider.get_nodes_by_field_prefix(
        collection=_CODE_FILES,
        field_name="filePath",
        prefix=prefix,
        filters={"orgId": org_id},
        limit=_LIST_SCAN_LIMIT,
    )
    files: list[dict[str, Any]] = []
    dirs: dict[str, int] = {}
    for raw in rows or []:
        row = _unwrap(raw)
        path = row.get("filePath")
        if not path or not path.startswith(prefix):
            continue
        rest = path[len(prefix):]
        if "/" in rest:
            child = rest.split("/", 1)[0]
            dirs[child] = dirs.get(child, 0) + 1
            continue
        files.append({
            "path": path,
            "language": row.get("language"),
            "role": row.get("fileRole"),
            "select": path,
        })
    scanned = len(rows or [])
    return {
        "directories": [
            {"path": f"{prefix}{name}", "files": n, "select": f"{prefix}{name}/"}
            for name, n in sorted(dirs.items(), key=lambda kv: (-kv[1], kv[0]))
        ][:limit],
        "files": sorted(files, key=lambda f: f["path"])[:limit],
        "truncated": scanned >= _LIST_SCAN_LIMIT or len(files) > limit or len(dirs) > limit,
    }


async def _select_by_qualified_name(
    graph_provider: Any, org_id: str, qualified_name: str, connector_id: str
) -> list[dict]:
    """Exact identity match, falling back to a casefolded one.

    Identity preserves case (`Foo` and `foo` are different symbols), but a model
    that retypes an address instead of copying it should still land. The exact
    match is tried first, so both spellings stay reachable in a file that has
    them.
    """
    base = {"orgId": org_id, "connectorId": connector_id}
    try:
        rows = await graph_provider.get_nodes_by_filters(
            collection=_BLOCKS, filters={**base, "qualifiedName": qualified_name}
        )
        if rows:
            return await _with_paths(graph_provider, org_id, [_unwrap(r) for r in rows])
        wanted = qualified_name.casefold()
        if wanted == qualified_name:
            return []
        rows = await graph_provider.get_nodes_by_filters(
            collection=_BLOCKS, filters={**base, "qualifiedName": wanted}
        )
    except Exception as exc:
        logger.warning("qualifiedName lookup failed for %s: %s", qualified_name, exc)
        return []
    return await _with_paths(graph_provider, org_id, [_unwrap(r) for r in rows or []])


async def _select_by_text(
    graph_provider: Any, org_id: str, text: str, connector_id: str
) -> list[dict]:
    """Rank blocks by how well their name matches the query.

    Finds a symbol by name inside one repo when the caller knows the connector
    but not the file -- the usual case straight after a knowledge search.
    """
    terms = _tokens(text)
    if not terms:
        return []
    rows = await graph_provider.search_nodes_by_field_terms(
        collection=_BLOCKS,
        field_name="name",
        terms=terms,
        filters={"orgId": org_id, "connectorId": connector_id},
        limit=_SELECT_SCAN_LIMIT,
    )
    return await _with_paths(graph_provider, org_id, [_unwrap(r) for r in rows])


async def _with_paths(
    graph_provider: Any, org_id: str, blocks: list[dict]
) -> list[dict]:
    """Join each block to its record's path; blocks do not store one."""
    await attach_file_paths(graph_provider, org_id, blocks)
    return blocks


def _unwrap(row: dict) -> dict:
    """Providers return either the node itself or {'b': node}."""
    if isinstance(row, dict) and len(row) <= 2 and ("b" in row or "node" in row):
        inner = row.get("b") or row.get("node")
        if isinstance(inner, dict):
            return inner
    return row


def _rank(blocks: list[dict], terms: list[str]) -> list[dict]:
    """Exact name match beats prefix beats substring; filler spans sink."""
    def score(block: dict) -> tuple:
        name = (block.get("name") or "").lower()
        kind = (block.get("kind") or "").lower()
        best = 0
        for term in terms:
            if name == term:
                best = max(best, 3)
            elif name.startswith(term):
                best = max(best, 2)
            elif term in name:
                best = max(best, 1)
        return (-best, kind in _NOISE_KINDS, len(name), block.get("filePath") or "")

    return sorted(blocks, key=score)


# ---------------------------------------------------------------------------
# Expansion
# ---------------------------------------------------------------------------

async def _expand(
    graph_provider: Any,
    seed_keys: list[str],
    relations: list[str],
    direction: str,
    depth: int,
) -> tuple[set[str], list[dict]]:
    """Walk `depth` hops, collecting every edge crossed."""
    visited = set(seed_keys)
    frontier = list(seed_keys)
    edges: list[dict] = []

    for _ in range(depth):
        if not frontier:
            break
        try:
            rows = await graph_provider.get_neighbors_for_nodes_by_relationship_types(
                node_keys=frontier[:_EXPAND_FANOUT],
                node_collection=_BLOCKS,
                relationship_types=relations,
                direction=direction,
                limit=_EXPAND_FANOUT,
            )
        except Exception as exc:
            logger.warning("Graph expansion failed: %s", exc)
            break

        next_frontier: list[str] = []
        for row in rows or []:
            key = row.get("key")
            anchor = row.get("anchorKey")
            if not key or not anchor:
                continue
            edges.append({
                "from": anchor,
                "to": key,
                "relation": row.get("relationshipType"),
                "direction": row.get("direction"),
                "collection": row.get("collection"),
                "line": row.get("line"),
                "confidence": row.get("confidence"),
            })
            if row.get("collection") == _BLOCKS and key not in visited:
                visited.add(key)
                next_frontier.append(key)
        frontier = next_frontier

    return visited, edges


# ---------------------------------------------------------------------------
# Grouping
#
# A module graph cannot be built from the blocks a selector happens to return:
# `backend/python/app/**` alone matches ~122k blocks, so any client-side sample
# lands entirely inside one directory and every edge it sees is a self-loop.
# The rollup therefore runs in the database, over the whole prefix, and only the
# aggregated rows come back.
# ---------------------------------------------------------------------------

# One row per (source file, relation, target file). Bounded by file pairs, not
# by blocks: over this repo 46k import edges aggregate to 24.5k rows and 134k
# call edges to 8.3k, so a whole-repo rollup fits with room to spare. Cutting it
# finer would silently skew the top-level view -- rows arrive in scan order, so
# a cap reached mid-repo drops whole directories rather than sampling evenly.
_ROLLUP_ROW_LIMIT = 100000


async def _accessible_records(graph_provider: Any, org_id: str, user_id: str) -> set[str] | None:
    """Every record the caller may read, in one query.

    The per-record gate used elsewhere is one traversal per record, which does
    not survive a whole-repo rollup. ``None`` means the bulk lookup is
    unavailable, and the caller must fall back rather than assume access.
    """
    try:
        mapping = await graph_provider.get_accessible_virtual_record_ids(
            user_id=user_id, org_id=org_id
        )
    except Exception as exc:
        logger.warning("Bulk permission lookup failed: %s", exc)
        return None
    return {r for r in (mapping or {}).values() if r}


async def _rollup_rows(
    graph_provider: Any,
    org_id: str,
    prefix: str,
    relations: list[str],
    direction: str,
    connector_id: str,
) -> list[dict]:
    """File-level edge counts for every block under ``prefix``.

    Each row carries the record id at both ends so the caller can drop what the
    user may not read; nothing is filtered here.
    """
    try:
        return await graph_provider.get_edge_rollup_by_file_prefix(
            connector_id=connector_id,
            org_id=org_id,
            file_path_prefix=prefix,
            relationship_types=relations,
            direction=direction,
            limit=_ROLLUP_ROW_LIMIT,
        )
    except Exception as exc:
        logger.warning("Module rollup failed: %s", exc)
        return []


def _group_rows(
    rows: list[dict],
    readable: set[str],
    record_paths: dict[str, str],
    group_by: str,
    depth: int,
) -> dict[str, Any]:
    """Roll aggregated record-level rows up to files or modules.

    The rollup aggregates by record because that is what an edge endpoint
    identifies; ``record_paths`` resolves those ids to paths in one batch, so a
    renamed file needs nothing rewritten in the graph.
    """
    def bucket(path: str | None) -> str | None:
        if not path:
            return None
        return path if group_by == "file" else _module_of(path, depth)

    members: dict[str, set[str]] = defaultdict(set)
    weights: Counter = Counter()
    relations: dict[tuple[str, str], Counter] = defaultdict(Counter)

    for row in rows:
        src_record, dst_record = row.get("srcRecord"), row.get("dstRecord")
        if src_record not in readable:
            continue
        src_path = record_paths.get(src_record)
        src = bucket(src_path)
        if src:
            members[src].add(src_path)
        # An unreadable target drops the dependency but not the module the
        # caller can see: hiding `web/ui` because of where it imports from
        # would be a different answer, not a redacted one.
        if dst_record not in readable:
            continue
        dst_path = record_paths.get(dst_record)
        dst = bucket(dst_path)
        if dst:
            members[dst].add(dst_path)
        if not src or not dst or src == dst:
            continue
        if row.get("dir") == "inbound":
            src, dst = dst, src
        count = int(row.get("n") or 1)
        weights[(src, dst)] += count
        relations[(src, dst)][row.get("rel")] += count

    return {
        "groups": [
            # `select` is the call that opens this group. Without it a rollup is
            # a dead end -- the model has a module name and no way to descend,
            # so it guesses file names instead.
            {
                "group": name,
                "files": len(paths),
                "select": f"{name}/**" if group_by == "directory" else name,
            }
            for name, paths in sorted(members.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        ],
        "edges": [
            {
                "from": src,
                "to": dst,
                "weight": weight,
                "relations": [r for r, _ in relations[(src, dst)].most_common(3)],
            }
            for (src, dst), weight in weights.most_common()
        ],
    }


async def _record_paths(graph_provider: Any, org_id: str, record_ids: set[str]) -> dict[str, str]:
    """filePath for a set of records, read from the record itself."""
    if not record_ids:
        return {}
    return await graph_provider.get_file_paths_for_records(
        org_id=org_id,
        record_ids=sorted(record_ids),
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def query_code_graph_impl(
    *,
    graph_provider: Any,
    org_id: str,
    user_id: str,
    connector_id: str,
    select: str,
    relations: list[str] | None = None,
    direction: str = "any",
    depth: int = 0,
    group_by: str = "none",
    kinds: list[str] | None = None,
    limit: int = DEFAULT_QUERY_LIMIT,
) -> dict[str, Any]:
    if not (connector_id or "").strip():
        return {"error": CONNECTOR_ID_REQUIRED}
    if not (select or "").strip():
        return {"error": "select is required: a name, a qualified name, or a path glob"}
    if direction not in ("outbound", "inbound", "any"):
        return {"error": "direction must be 'outbound', 'inbound' or 'any'"}
    if group_by not in GROUP_BY_CHOICES:
        return {"error": f"group_by must be one of {', '.join(GROUP_BY_CHOICES)}"}

    depth = max(0, min(int(depth or 0), MAX_QUERY_DEPTH))
    limit = max(1, min(int(limit or DEFAULT_QUERY_LIMIT), MAX_QUERY_LIMIT))
    relations = [r for r in (relations or ALL_RELATIONS) if r in ALL_RELATIONS] or ALL_RELATIONS
    wanted_kinds = {k.strip().lower() for k in (kinds or []) if k and k.strip()}

    select = select.strip()

    # A directory is an inventory question, not a dependency one: answer it
    # before the grouped/ungrouped split so `relations`/`depth` never apply.
    if group_by == "none" and _looks_like_directory(select):
        listing = await _list_children(
            graph_provider, org_id, select, connector_id, limit
        )
        return {
            "select": select,
            "resolved_as": "directory",
            "connector_id": connector_id,
            **listing,
        }

    if group_by != "none":
        if depth:
            return {"error": (
                "depth does not apply when group_by is set — a rollup always "
                "aggregates direct dependencies. Drop depth, or drop group_by."
            )}
        return await _grouped_result(
            graph_provider=graph_provider, org_id=org_id, user_id=user_id,
            connector_id=connector_id, select=select, relations=relations,
            direction=direction, group_by=group_by, limit=limit,
        )

    scan_capped = False
    if _looks_like_path(select):
        matched, scan_capped = await _select_by_path(
            graph_provider, org_id, select, connector_id
        )
        how = "path"
    else:
        matched = await _select_by_qualified_name(graph_provider, org_id, select, connector_id)
        how = "qualified_name"
        if not matched:
            matched = await _select_by_text(graph_provider, org_id, select, connector_id)
            how = "text"
            matched = _rank(matched, _tokens(select))
            matched = [m for m in matched if (m.get("kind") or "").lower() not in _NOISE_KINDS] or matched

    # Gate before counting. `matches: 3` for a path the caller cannot read still
    # discloses that the file exists and how much is in it, so a denial and a
    # miss have to produce the same payload.
    if wanted_kinds:
        # A path selector returns every span that tiles the file -- imports,
        # statements, header -- which is roughly half the payload. The noise
        # filter only runs on the text branch, so this is the way to ask a file
        # for its definitions.
        matched = [m for m in matched if (m.get("kind") or "").lower() in wanted_kinds]

    allowed: dict[str, bool] = {}
    matched = await _readable_only(graph_provider, org_id, user_id, matched, allowed)

    if not matched:
        return {
            "select": select, "resolved_as": how, "connector_id": connector_id,
            "matches": 0, "nodes": [], "edges": [], "truncated": False,
            # A dead end is where the model most needs a next step. Without one
            # it guesses another name -- three wasted calls in the trace this
            # was written from.
            "hint": _miss_hint(select, how),
        }

    total_matched = len(matched)
    seeds = matched[:limit]
    seed_keys = [m.get("_key") or m.get("id") for m in seeds if (m.get("_key") or m.get("id"))]

    visited, edges = ({k for k in seed_keys}, [])
    if depth:
        visited, edges = await _expand(graph_provider, seed_keys, relations, direction, depth)

    blocks = await _readable_blocks(graph_provider, org_id, user_id, sorted(visited), allowed,
                                     connector_id=connector_id)
    edges = [e for e in edges if e["from"] in blocks
             and (e.get("collection") == _RECORDS or e["to"] in blocks)]

    result: dict[str, Any] = {
        "select": select,
        "resolved_as": how,
        # Echoed once rather than on every ref: it is constant for the whole
        # result, and every follow-up call requires it. Without it the output
        # of one tool is not valid input to the next.
        "connector_id": connector_id,
        "matches": total_matched,
        # Silence reads as absence: a model that thinks it saw everything will
        # state a wrong conclusion confidently.
        "truncated": total_matched > len(seeds) or scan_capped,
    }
    if scan_capped:
        result["scan_capped"] = True

    result["nodes"] = [SymbolRef.from_block(b) for b in blocks.values()][:limit]
    result["edges"] = [_edge_ref(blocks, e) for e in edges[:limit]]
    return result


def _miss_hint(select: str, how: str) -> str:
    """What to call next when a selector matched nothing.

    Each branch fails for a different reason, so a single "not found" would
    send the model guessing. A path that misses usually means the file does not
    exist under that name; a qualified name that misses usually means the right
    file with the wrong symbol.
    """
    if how == "path":
        parent = select.rstrip("/").rpartition("/")[0]
        where = f"{parent}/" if parent else "**"
        return (
            f"No file matched {select!r}. List what is actually there with "
            f"select={where!r}, then re-select an exact path."
        )
    if how == "qualified_name":
        return (
            f"No symbol named {select!r} in this repository. If you know the "
            "file, select its path to list the symbols it defines; otherwise "
            "search for it by wording."
        )
    return (
        f"Nothing matched {select!r}. Free text matches symbol names only — "
        "select a path to list a directory or file, or search the knowledge "
        "base for wording that appears in the code itself."
    )


def _edge_ref(blocks: dict[str, dict], edge: dict) -> dict[str, str]:
    """Report an edge the way it points, not the way it was walked.

    `_expand` returns (anchor, neighbour); on an inbound hop the neighbour is
    the caller, so printing that pair verbatim says the callee calls its own
    callers.
    """
    anchor, other = edge["from"], edge["to"]
    if edge.get("direction") == "inbound":
        anchor, other = other, anchor
    out = {
        "from": _ref(blocks, anchor),
        "to": _ref(blocks, other),
        "relation": edge["relation"],
    }
    # The call site, so a caller is a place to read rather than a name to
    # hunt for; and whether the resolver proved the edge or inferred it.
    if edge.get("line") is not None:
        out["line"] = edge["line"]
    if edge.get("confidence"):
        out["confidence"] = edge["confidence"]
    return out


async def _grouped_result(
    *,
    graph_provider: Any,
    org_id: str,
    user_id: str,
    connector_id: str,
    select: str,
    relations: list[str],
    direction: str,
    group_by: str,
    limit: int,
) -> dict[str, Any]:
    """The module view: every edge under a path prefix, rolled up.

    A non-path selector has no prefix to aggregate over, so it is resolved to
    the directories of its matches first and each of those is rolled up.
    """
    if _looks_like_path(select):
        prefix, how = select.split("*", 1)[0], "path"
    else:
        matched = await _select_by_qualified_name(graph_provider, org_id, select, connector_id)
        how = "qualified_name"
        if not matched:
            matched = _rank(
                await _select_by_text(graph_provider, org_id, select, connector_id),
                _tokens(select),
            )
            how = "text"
        paths = [m.get("filePath") for m in matched[:limit] if m.get("filePath")]
        prefix = posixpath.commonprefix(paths) if paths else ""
        prefix = prefix.rsplit("/", 1)[0] + "/" if "/" in prefix else prefix

    readable = await _accessible_records(graph_provider, org_id, user_id)
    if readable is None:
        # An infrastructure failure, not a permission decision — saying so
        # reveals nothing about any record, and beats reporting "no
        # dependencies" for something the model will then state as fact.
        return {"error": "Permission lookup is unavailable; cannot build a module view."}
    if not readable:
        return {"select": select, "resolved_as": how, "grouped_by": group_by,
                "groups": [], "edges": [], "truncated": False}

    rows = await _rollup_rows(
        graph_provider, org_id, prefix, relations, direction, connector_id
    )
    # Both endpoints are record ids; resolve every readable one to a path in a
    # single batch rather than carrying ~30 copies of each path on the blocks.
    wanted = {r.get(end) for r in rows for end in ("srcRecord", "dstRecord")} & readable
    paths = await _record_paths(graph_provider, org_id, {r for r in wanted if r})

    module_depth = _module_depth_for(prefix) if group_by == "directory" else 0
    result = _group_rows(rows, readable, paths, group_by, module_depth)

    # `limit` bounds BOTH lists. Capping only edges let one call return a
    # 3,761-entry group list -- 328KB of a 357KB payload -- while reporting
    # itself as within limit.
    total_edges, total_groups = len(result["edges"]), len(result["groups"])
    result["edges"] = result["edges"][:limit]
    result["groups"] = result["groups"][:limit]
    return {
        "select": select,
        "resolved_as": how,
        "scope": prefix or "(everything)",
        "grouped_by": group_by,
        "connector_id": connector_id,
        "dependencies": total_edges,
        "modules": total_groups,
        # Silence reads as absence: a model that thinks it saw everything will
        # state a wrong conclusion confidently.
        "truncated": (
            total_edges > limit
            or total_groups > limit
            or len(rows) >= _ROLLUP_ROW_LIMIT
        ),
        **result,
    }


def _ref(blocks: dict[str, dict], key: str) -> str:
    block = blocks.get(key)
    if not block:
        return key
    return f"{block.get('filePath') or ''}#{block.get('qualifiedName') or ''}".strip("#")


async def _readable_only(
    graph_provider: Any,
    org_id: str,
    user_id: str,
    blocks: list[dict],
    allowed: dict[str, bool],
) -> list[dict]:
    """Drop blocks whose owning record the caller cannot read, keeping order."""
    out = []
    for block in blocks:
        record_id = block.get("recordId")
        if record_id not in allowed:
            allowed[record_id] = await _user_can_read(graph_provider, user_id, org_id, record_id)
        if allowed[record_id]:
            out.append(block)
    return out


