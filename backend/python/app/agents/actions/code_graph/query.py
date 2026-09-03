"""Structural lookup over the code graph: what is in a place.

Two call shapes, and only two:

    select="src/api/"                    -> the files and subdirectories in it
    select="src/api/client.py"           -> the symbols that file defines
    select="frontend/**"                 -> the symbols under a subtree

A symbol is deliberately not one of them. Selecting one could only echo the
address the caller just typed, or return its edges -- and edges are
``get_neighbour``'s job, reading is ``read_code``'s. Free text is not accepted
either: a description becomes a path through a knowledge search, not here.

Kept separate from ``ops.py`` so the narrow tools stay readable; both share
``ops``' access-control helpers.
"""
from __future__ import annotations

import asyncio
import fnmatch
import logging
from typing import Any

from app.config.constants.arangodb import CollectionNames, RecordRelations
from app.modules.parsers.code_parser.models import FILLER_KINDS

from .ops import (
    TEST_ROLE,
    SymbolRef,
    _readable_blocks,
    _user_can_read,
    get_accessible_record_ids,
    get_record_roles,
)

logger = logging.getLogger(__name__)

__all__ = [
    "CONNECTOR_ID_REQUIRED",
    "DEFAULT_QUERY_LIMIT",
    "QUERY_CODE_GRAPH_TOOL_NAME",
    "query_code_graph_impl",
]

# The granted spelling, for prompt text that has to name the tool. Mirrors
# `FETCH_RECORD_TOOL_NAME`; a prompt that names a tool the caller cannot call is
# worse than one that stays silent.
QUERY_CODE_GRAPH_TOOL_NAME = "codegraph__query_code_graph"

_BLOCKS = CollectionNames.BLOCKS.value
_CODE_FILES = CollectionNames.CODE_FILES.value

CONNECTOR_ID_REQUIRED = (
    "connector_id is required. Run a knowledge search first and copy the "
    "`Connector ID` shown on any record from the repository you mean — the code "
    "graph spans every indexed repo, so it has to be told which one."
)

DEFAULT_QUERY_LIMIT = 50
MAX_QUERY_LIMIT = 200

ALL_RELATIONS = [r.value for r in RecordRelations]

# Filler spans exist so the blocks of a file tile it exactly. Ranked below the
# real definitions rather than dropped: they are still what the file contains,
# but every file has an imports block and none of them answers a question.
# Sourced from the parser so a new filler kind needs no second edit.
_NOISE_KINDS = FILLER_KINDS | {"file_summary"}

# How many FILES a path selector may match.
_SELECT_SCAN_LIMIT = 400
# Files whose blocks a single path selector will actually load.
_PATH_FANOUT_FILES = 60
# Files a directory listing may scan before it reports itself truncated.
_LIST_SCAN_LIMIT = 2000
# Degree is counted for at most this many candidates, over at most this many
# edge rows. Both caps are reported rather than applied silently: a truncated
# count ranks the wrong symbol first, which is worse than no ranking at all.
_DEGREE_CANDIDATES = 400
_DEGREE_ROW_LIMIT = 50000

_CODE_EXTENSIONS = (".py", ".ts", ".tsx", ".js", ".jsx")


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


def _looks_like_locator(select: str) -> bool:
    """``path/to/file.py#function:main`` -- a file and a symbol in one string.

    A qualified name is unique only within a file, so a bare
    ``function:main`` is ambiguous across a repository. This is how a caller
    says *which* one, and it is the form every result already prints.
    """
    path, sep, qualified = select.partition("#")
    return bool(sep and path.strip() and qualified.strip())


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


def _unwrap(row: dict) -> dict:
    """Providers return either the node itself or {'b': node}."""
    if isinstance(row, dict) and len(row) <= 2 and ("b" in row or "node" in row):
        inner = row.get("b") or row.get("node")
        if isinstance(inner, dict):
            return inner
    return row


def _rank_by_degree(blocks: list[dict], degrees: dict[str, int]) -> list[dict]:
    """Most-connected first.

    A path selector carries no name to match on, so degree is the only thing
    separating a file's entry points from its one-line helpers. Without it the
    result arrives in scan order and a 60-file selection reads as a flat list.
    """
    def score(block: dict) -> tuple:
        return (
            (block.get("kind") or "").lower() in _NOISE_KINDS,
            -degrees.get(_key_of(block), 0),
            block.get("filePath") or "",
            block.get("startLine") or 0,
        )

    return sorted(blocks, key=score)


def _key_of(block: dict) -> str:
    return block.get("_key") or block.get("id") or ""


async def _degrees(
    graph_provider: Any, blocks: list[dict], relations: list[str]
) -> tuple[dict[str, int], bool]:
    """How many edges touch each block, in one batched call.

    Counted undirected, and only over ``relations`` -- degree answers "how
    central is this, for the relationship I asked about", so a CALLS-only query
    should not rank by import count.

    A symbol forty places call and one that calls forty are both hubs, and
    either is worth surfacing first.

    Returns ``(counts, capped)``. When the row cap bites the counts are
    truncated in scan order, which ranks the wrong symbol first -- so the
    caller reports it rather than presenting a skewed order as a ranking.
    """
    keys = [k for k in (_key_of(b) for b in blocks[:_DEGREE_CANDIDATES]) if k]
    if len(keys) < 2:
        return {}, False
    try:
        rows = await graph_provider.get_neighbors_for_nodes_by_relationship_types(
            node_keys=keys,
            node_collection=_BLOCKS,
            relationship_types=relations,
            direction="any",
            limit=_DEGREE_ROW_LIMIT,
        )
    except Exception as exc:
        logger.warning("Degree lookup failed: %s", exc)
        return {}, False
    counts: Counter = Counter()
    for row in rows or []:
        anchor = row.get("anchorKey")
        if anchor:
            counts[anchor] += 1
    capped = len(rows or []) >= _DEGREE_ROW_LIMIT or len(blocks) > _DEGREE_CANDIDATES
    return dict(counts), capped


# ---------------------------------------------------------------------------
# Expansion
# ---------------------------------------------------------------------------

async def query_code_graph_impl(
    *,
    graph_provider: Any,
    org_id: str,
    user_id: str,
    connector_id: str,
    select: str,
    kinds: list[str] | None = None,
    limit: int = DEFAULT_QUERY_LIMIT,
    include_tests: bool = False,
) -> dict[str, Any]:
    if not (connector_id or "").strip():
        return {"error": CONNECTOR_ID_REQUIRED}
    if not (select or "").strip():
        return {"error": "select is required: a directory or a file path"}

    limit = max(1, min(int(limit or DEFAULT_QUERY_LIMIT), MAX_QUERY_LIMIT))
    wanted_kinds = {k.strip().lower() for k in (kinds or []) if k and k.strip()}

    select = select.strip()

    # This tool answers "what is here", and nothing else. A symbol has two
    # possible answers and neither belongs here: echo the address the caller
    # just typed, or return its edges — which is `get_neighbour`.
    if _looks_like_locator(select):
        return {"error": (
            f"{select!r} names one symbol. `read_code` reads it, `get_neighbour` "
            "walks its edges, and selecting its file lists every symbol the file "
            "defines."
        )}
    if not _looks_like_path(select) and not _looks_like_directory(select):
        return {"error": (
            f"{select!r} is not a path. Select a directory to list what is in it, "
            "or a file to list the symbols it defines. Search the knowledge base "
            "to find a path first."
        )}

    if _looks_like_directory(select):
        listing = await _list_children(
            graph_provider, org_id, select, connector_id, limit
        )
        # An empty listing means this was never a directory. `conversations/
        # stream` is a URL fragment; answering it with "no such directory" is a
        # dead end for something free text finds.
        if not include_tests:
            listing["files"] = [f for f in listing["files"] if f.get("role") != TEST_ROLE]
        if listing["directories"] or listing["files"]:
            return {
                "select": select,
                "resolved_as": "directory",
                "connector_id": connector_id,
                **listing,
            }

    matched, scan_capped = await _select_by_path(
        graph_provider, org_id, select, connector_id
    )
    how = "path"

    # Gate before counting. `matches: 3` for a path the caller cannot read still
    # discloses that the file exists and how much is in it, so a denial and a
    # miss have to produce the same payload.
    if wanted_kinds:
        matched = [m for m in matched if (m.get("kind") or "").lower() in wanted_kinds]

    _rids = {b.get("recordId") for b in matched if b.get("recordId")}
    coros: list = [get_accessible_record_ids(graph_provider, org_id, user_id)]
    if not include_tests:
        coros.append(get_record_roles(graph_provider, org_id, _rids))
    results = await asyncio.gather(*coros)
    accessible: set[str] | None = results[0]
    if not include_tests:
        _roles: dict[str, str] = results[1]
        matched = [b for b in matched if _roles.get(b.get("recordId")) != TEST_ROLE]

    matched = await _readable_only(graph_provider, org_id, user_id, matched, accessible)

    if not matched:
        return {
            "select": select, "resolved_as": how, "connector_id": connector_id,
            "matches": 0, "nodes": [], "edges": [], "truncated": False,
            # A dead end is where the model most needs a next step. Without one
            # it guesses another name -- three wasted calls in the trace this
            # was written from.
            "hint": _miss_hint(select, how),
        }

    # Rank after gating, not before: ranking a set that is then filtered spends
    # the top slots on rows the caller never sees. Degree counts every code
    # relation: a path carries no name to match on, so it is the only signal
    # separating an entry point from a helper.
    degrees, degree_capped = await _degrees(graph_provider, matched, ALL_RELATIONS)
    matched = _rank_by_degree(matched, degrees)

    total_matched = len(matched)
    seeds = matched[:limit]
    seed_keys = [k for m in seeds if (k := _key_of(m))]

    blocks = await _readable_blocks(graph_provider, org_id, user_id, sorted(seed_keys),
                                     accessible=accessible, connector_id=connector_id)

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
    if degree_capped:
        # Counts truncate in scan order, so the ordering is not a ranking.
        result["degree_capped"] = True

    # The batch load returns blocks keyed arbitrarily, which discarded the
    # ranking.
    ordered = [k for k in seed_keys if k in blocks]

    nodes = []
    for key in ordered[:limit]:
        ref = SymbolRef.from_block(blocks[key])
        if key in degrees:
            # How many edges touch this symbol. The one number that separates a
            # hub from a leaf, and the thing to quote when ranking a claim.
            ref["degree"] = degrees[key]
        nodes.append(ref)
    result["nodes"] = nodes
    if nodes:
        result["next"] = (
            "These are addresses, not relationships. `read_code` reads one; "
            "`get_neighbour` walks what it reaches or what reaches it. Rank by "
            "`degree` to pick which."
        )
    return result


def _miss_hint(select: str, how: str) -> str:
    """What to call next when a selector matched nothing.

    Each branch fails for a different reason, so a single "not found" would
    send the model guessing. A path that misses usually means the file does not
    exist under that name; a qualified name that misses usually means the right
    file with the wrong symbol.
    """
    if how == "locator":
        file_path, _, qualified_name = select.partition("#")
        return (
            f"No symbol {qualified_name!r} in {file_path!r}. The file may still "
            f"exist — select {file_path!r} on its own to list what it defines, "
            "then copy an exact name from that."
        )
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


async def _readable_only(
    graph_provider: Any,
    org_id: str,
    user_id: str,
    blocks: list[dict],
    accessible: set[str] | None = None,
) -> list[dict]:
    """Drop blocks whose owning record the caller cannot read, keeping order.

    When ``accessible`` is provided (from ``get_accessible_record_ids``), the
    check is an O(1) set lookup per block.  Otherwise falls back to parallel
    per-record ``_user_can_read`` calls.
    """
    if accessible is not None:
        return [b for b in blocks if b.get("recordId") in accessible]

    unique_rids = list({b.get("recordId") for b in blocks if b.get("recordId")})
    if not unique_rids:
        return []
    verdicts = await asyncio.gather(*(
        _user_can_read(graph_provider, user_id, org_id, rid)
        for rid in unique_rids
    ))
    allowed = dict(zip(unique_rids, verdicts))
    return [b for b in blocks if allowed.get(b.get("recordId"), False)]


