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

import fnmatch
import logging
import re
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames, RecordRelations
from app.modules.parsers.code_parser.models import FILLER_KINDS

from .ops import (
    TEST_ROLE,
    RecordAccess,
    SymbolRef,
    _degrees,
    _key_of,
    _readable_blocks,
    _readable_only,
    _repo_matches,
    _unwrap,
    attach_file_paths,
    coverage_for,
    get_record_roles,
    repo_names,
)

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

logger = logging.getLogger(__name__)

__all__ = [
    "CONNECTOR_ID_REQUIRED",
    "DEFAULT_QUERY_LIMIT",
    "QUERY_CODE_GRAPH_TOOL_NAME",
    "DEFAULT_ENTRY_LIMIT",
    "entry_points_impl",
    "files_by_pattern",
    "query_code_graph_impl",
]

# The granted spelling, for prompt text that has to name the tool. Mirrors
# `FETCH_RECORD_TOOL_NAME`; a prompt that names a tool the caller cannot call is
# worse than one that stays silent.
QUERY_CODE_GRAPH_TOOL_NAME = "codegraph__query_code_graph"

_BLOCKS = CollectionNames.BLOCKS.value
_CODE_FILES = CollectionNames.CODE_FILES.value
_RECORDS = CollectionNames.RECORDS.value

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
_CODE_EXTENSIONS = (".py", ".ts", ".tsx", ".js", ".jsx")


def _looks_like_path(select: str) -> bool:
    return "/" in select or "*" in select or select.endswith(_CODE_EXTENSIONS)


def _looks_like_directory(select: str) -> bool:
    """A path with no glob and no file extension -- list its children.

    `app/agents/` and `app/agents` both mean "what is in here". Without this
    they take the prefix branch and return a jumble of blocks from whichever
    files scanned first, which is never what the caller wanted.

    Paths are stored repo-relative, so every top-level directory is slash-free:
    `backend` has to be tried as one. Nothing in the string separates it from
    free text like `stream_record` -- only the listing does, so a bare name is
    a candidate here and is rejected after it fails to resolve.
    """
    if "*" in select or "?" in select:
        return False
    # No path segment holds a space, and `:` and `#` belong to the symbol forms
    # this tool refuses outright -- those need no lookup to turn away.
    if any(c in select for c in " \t:#"):
        return False
    return not select.endswith(_CODE_EXTENSIONS)


def _looks_like_locator(select: str) -> bool:
    """``path/to/file.py#function:main`` -- a file and a symbol in one string.

    A qualified name is unique only within a file, so a bare
    ``function:main`` is ambiguous across a repository. This is how a caller
    says *which* one, and it is the form every result already prints.
    """
    path, sep, qualified = select.partition("#")
    return bool(sep and path.strip() and qualified.strip())


def path_matches(path: str, pattern: str) -> bool:
    """A glob matches by ``fnmatch``; a literal is the file itself or a directory above it,
    never a name prefix: ``app/page.ts`` does not match ``app/page.tsx``."""
    if "*" in pattern or "?" in pattern:
        return fnmatch.fnmatch(path, pattern)
    literal = pattern.rstrip("/")
    return not literal or path == literal or path.startswith(literal + "/")


async def files_by_pattern(
    graph_provider: IGraphDBProvider, org_id: str, pattern: str, *, scan_limit: int | None = None,
) -> tuple[dict[str, str], bool]:
    """``(record id -> path, scan_capped)`` for the code files a glob, file or directory matches.

    Matched against ``codeFiles``, one row per file. Neither connector nor
    access is applied here: callers re-apply both.
    """
    # Anchor the DB-side filter on the pattern's literal prefix so the index does
    # the work; the glob itself is applied in Python.
    prefix = pattern.split("*", 1)[0]
    scan_limit = scan_limit or _SELECT_SCAN_LIMIT
    files = await graph_provider.get_nodes_by_field_prefix(
        collection=_CODE_FILES,
        field_name="filePath",
        prefix=prefix,
        filters={"orgId": org_id},
        limit=scan_limit,
        raise_on_error=True,
        return_fields=["_key", "filePath"],
    )
    paths: dict[str, str] = {}
    for raw in files or []:
        row = _unwrap(raw)
        key = row.get("_key") or row.get("id")
        path = row.get("filePath")
        if not key or not path or not path_matches(path, pattern):
            continue
        paths[key] = path
    return paths, len(files or []) >= scan_limit


async def _select_by_path(
    graph_provider: IGraphDBProvider, org_id: str, pattern: str, connector_id: str
) -> tuple[list[dict], bool]:
    """Blocks of the files whose path matches a glob (or a literal prefix).

    Matched against ``codeFiles``, which holds one row per file, rather than
    against blocks, which hold ~30 copies of the same path. The scan limit
    therefore counts files, not symbols.

    Returns ``(rows, scan_capped)``. Both caps bite silently otherwise: the
    caller counts what it got and would report a clean ``truncated: false``
    for a prefix that was cut short.
    """
    paths, files_capped = await files_by_pattern(graph_provider, org_id, pattern)
    if not paths:
        return [], False

    # Bound the fan-out. Files are cheap to list, blocks are not: `backend/**`
    # matches thousands of files at ~30 blocks each, and an unbounded IN would
    # pull the whole repo into memory to answer one call.
    # By path, not record id: the sample a capped glob shows is a stable,
    # readable slice of the tree (A-12).
    capped = sorted(paths, key=lambda key: (paths[key], key))[:_PATH_FANOUT_FILES]
    scan_capped = files_capped or len(capped) < len(paths)
    rows = await graph_provider.get_nodes_by_field_in(
        collection=_BLOCKS, field_name="recordId", field_values=capped, raise_on_error=True
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


async def _visible_record_ids(
    graph_provider: IGraphDBProvider,
    org_id: str,
    user_id: str,
    connector_id: str,
    record_ids: set[str],
) -> set[str]:
    """Which of ``record_ids`` are in this connector and readable by the caller.

    ``codeFiles`` is keyed by record id and carries neither ``connectorId`` nor
    permissions, so both are re-applied from the owning ``records`` row. Fails
    closed: a record whose connector or access cannot be established is dropped,
    so an unavailable lookup narrows the answer rather than widening it.
    """
    if not record_ids:
        return set()
    candidates = sorted(await RecordAccess(graph_provider, org_id, user_id).readable(record_ids))
    if not candidates:
        return set()
    # Raises on failure: an empty set would list the directory as empty.
    rows = await graph_provider.get_nodes_by_field_in(
        collection=_RECORDS,
        field_name="_key",
        field_values=candidates,
        return_fields=["_key", "orgId", "connectorId"],
        raise_on_error=True,
    )
    visible: set[str] = set()
    for raw in rows or []:
        row = _unwrap(raw)
        if row.get("orgId") != org_id or row.get("connectorId") != connector_id:
            continue
        if key := (row.get("_key") or row.get("id")):
            visible.add(key)
    return visible


async def _list_children(
    graph_provider: IGraphDBProvider,
    org_id: str,
    user_id: str,
    prefix: str,
    connector_id: str,
    limit: int,
) -> dict[str, Any]:
    """Immediate files and subdirectories under a directory prefix.

    Reads ``codeFiles`` rather than deriving from blocks: the rollup in
    `_group_rows` is built from edge rows, so a file nothing imports would be
    invisible there. An inventory has to come from the file list itself.

    The prefix scan is only org-scoped, so a path two repos share would
    otherwise list files from a connector the caller did not ask for and may not
    be allowed to read. Both the subdirectory rollup and the file list are built
    from the gated rows only.
    """
    prefix = prefix.rstrip("/") + "/" if prefix.strip("/") else ""
    rows = [
        _unwrap(raw)
        for raw in await graph_provider.get_nodes_by_field_prefix(
            collection=_CODE_FILES,
            field_name="filePath",
            prefix=prefix,
            filters={"orgId": org_id},
            limit=_LIST_SCAN_LIMIT,
            raise_on_error=True,
        ) or []
    ]
    visible = await _visible_record_ids(
        graph_provider, org_id, user_id, connector_id,
        {key for row in rows if (key := (row.get("_key") or row.get("id")))},
    )
    files: list[dict[str, Any]] = []
    dirs: dict[str, int] = {}
    for row in rows:
        path = row.get("filePath")
        if not path or not path.startswith(prefix):
            continue
        if (row.get("_key") or row.get("id")) not in visible:
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
    scanned = len(rows)
    return {
        "directories": [
            {"path": f"{prefix}{name}", "files": n, "select": f"{prefix}{name}/"}
            for name, n in sorted(dirs.items(), key=lambda kv: (-kv[1], kv[0]))
        ][:limit],
        "files": sorted(files, key=lambda f: f["path"])[:limit],
        "truncated": scanned >= _LIST_SCAN_LIMIT or len(files) > limit or len(dirs) > limit,
    }


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


# ---------------------------------------------------------------------------
# Expansion
# ---------------------------------------------------------------------------

async def query_code_graph_impl(
    *,
    graph_provider: IGraphDBProvider,
    org_id: str,
    user_id: str,
    connector_id: str,
    select: str,
    kinds: list[str] | None = None,
    limit: int = DEFAULT_QUERY_LIMIT,
    include_tests: bool = False,
    repo: str | None = None,
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

    if _looks_like_directory(select):
        listing = await _list_children(
            graph_provider, org_id, user_id, select, connector_id, limit
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

    # A name with no separator is a selector only if it just resolved as a
    # directory. `app/agents` still falls through to the prefix scan, but
    # `stream_record` is free text and the prefix scan cannot say so.
    if not _looks_like_path(select):
        return {"error": (
            f"{select!r} is not a path. Select a directory to list what is in it, "
            "or a file to list the symbols it defines. Search the knowledge base "
            "to find a path first."
        )}

    matched, scan_capped = await _select_by_path(
        graph_provider, org_id, select, connector_id
    )
    how = "path"

    # Gate before counting. `matches: 3` for a path the caller cannot read still
    # discloses that the file exists and how much is in it, so a denial and a
    # miss have to produce the same payload.
    if wanted_kinds:
        matched = [m for m in matched if (m.get("kind") or "").lower() in wanted_kinds]
    if repo:
        names = await repo_names(graph_provider, org_id, (m.get("recordGroupId") for m in matched))
        matched = [m for m in matched if _repo_matches(repo, m.get("recordGroupId"), names)]

    access = RecordAccess(graph_provider, org_id, user_id)
    # Readable first: the hidden-test count must not reveal unreadable files.
    matched = await _readable_only(access, matched)

    hidden_tests = 0
    if not include_tests:
        _rids = {b.get("recordId") for b in matched if b.get("recordId")}
        _roles = await get_record_roles(graph_provider, org_id, _rids)
        kept = [b for b in matched if _roles.get(b.get("recordId")) != TEST_ROLE]
        # Files, not blocks: "3 test files hidden" is what the model can act on (A-12).
        hidden_tests = len({b.get("recordId") for b in matched} - {b.get("recordId") for b in kept})
        matched = kept

    if not matched:
        empty: dict[str, Any] = {
            "select": select, "resolved_as": how, "connector_id": connector_id,
            # Empty after a capped scan is not the same as empty: files past the
            # scan or fan-out limit were never loaded, so the miss is unproven.
            "matches": 0, "nodes": [], "truncated": scan_capped,
            # A dead end is where the model most needs a next step. Without one
            # it guesses another name -- three wasted calls in the trace this
            # was written from.
            "hint": _miss_hint(select, how),
        }
        if scan_capped:
            empty["scan_capped"] = True
        if hidden_tests:
            empty["hidden_tests"] = hidden_tests
            empty["hint"] = (f"{hidden_tests} test file(s) matched and were hidden; "
                             "set include_tests=true to see them.")
        return empty

    # Rank after gating, not before: ranking a set that is then filtered spends
    # the top slots on rows the caller never sees. Degree counts every code
    # relation: a path carries no name to match on, so it is the only signal
    # separating an entry point from a helper.
    degrees, degree_capped = await _degrees(graph_provider, matched, ALL_RELATIONS, org_id, access)
    matched = _rank_by_degree(matched, degrees)

    total_matched = len(matched)
    seeds = matched[:limit]
    seed_keys = [k for m in seeds if (k := _key_of(m))]

    blocks = await _readable_blocks(graph_provider, org_id, access, sorted(seed_keys),
                                     connector_id=connector_id)

    result: dict[str, Any] = {
        "select": select,
        "resolved_as": how,
        # Echoed once rather than on every ref: it is constant for the whole
        # result, and every follow-up call requires it. Without it the output
        # of one tool is not valid input to the next.
        "connector_id": connector_id,
        **({"coverage": gap} if (gap := coverage_for(matched)) else {}),
        "matches": total_matched,
        # Silence reads as absence: a model that thinks it saw everything will
        # state a wrong conclusion confidently.
        "truncated": total_matched > len(seeds) or scan_capped,
    }
    if scan_capped:
        result["scan_capped"] = True
    if hidden_tests:
        result["hidden_tests"] = hidden_tests
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
            "`get_neighbour` walks what it reaches or what reaches it — it also "
            "takes a bare file path, with no symbol. Rank by `degree` to pick which."
        )
        if scan_capped:
            # A capped glob looks like an answer and is a sample: the model
            # reads the top of an arbitrary slice as if it were the subtree.
            # Naming the directory listing is the only way it learns there is
            # an exact alternative.
            result["next"] = (
                "This is a SAMPLE, not the subtree — the scan stopped early, so "
                "files past the cap were never looked at. To see what is really "
                f"there, select {_glob_parent(select)!r} and drill down one level "
                "at a time: a directory listing is exact and drops nothing. "
            ) + result["next"]
    return result


def _glob_parent(select: str) -> str:
    """The directory a glob was reaching into, as a `select` value."""
    head = select.split("*", 1)[0]
    parent = head.rstrip("/") if head.endswith("/") else head.rpartition("/")[0]
    return f"{parent}/" if parent else "**"


def _miss_hint(select: str, how: str) -> str:
    """What to call next when a selector matched nothing.

    A path that misses usually means the file does not exist under that name, so
    the next step is a listing of its parent rather than another guessed path.
    """
    if how == "path":
        parent = select.rstrip("/").rpartition("/")[0]
        where = f"{parent}/" if parent else "**"
        return (
            f"No file matched {select!r}. List what is actually there with "
            f"select={where!r}, then re-select an exact path."
        )
    return (
        f"Nothing matched {select!r}. Free text matches symbol names only — "
        "select a path to list a directory or file, or search the knowledge "
        "base for wording that appears in the code itself."
    )


# Where a reader starts: names and files that are entry points by convention.
_ENTRY_NAMES = re.compile(
    r"^(?:main|run|start|serve|app|create_app|cli|handler|lambda_handler|handle\w*"
    r"|on_\w+|get|post|put|patch|delete|index|setup|bootstrap|init_app)$",
    re.IGNORECASE,
)
_ENTRY_FILES = re.compile(
    r"(?:^|/)(?:main|app|server|cli|index|wsgi|asgi|manage|__main__)\.\w+$"
    r"|(?:^|/)(?:routes?|handlers?|views?|controllers?|api|endpoints?|commands?|jobs?)/",
    re.IGNORECASE,
)
_ENTRY_KINDS = {"function", "method"}
_ENTRY_SCAN_BLOCKS = 5000
_ENTRY_BATCH = 400
DEFAULT_ENTRY_LIMIT = 30


async def entry_points_impl(
    *,
    graph_provider: IGraphDBProvider,
    connector_id: str,
    org_id: str,
    user_id: str,
    repo: str | None = None,
    limit: int = DEFAULT_ENTRY_LIMIT,
    include_tests: bool = False,
) -> dict[str, Any]:
    """Functions and methods nothing in the repo calls, conventional entry points first (A-9).

    Code with no caller in the graph is where control arrives from outside: a
    route, a CLI, a job, a framework hook. Conventional names and files rank
    first; the rest follow by path. Calls in languages without import resolution
    are matched by name, and some are left unresolved, so a function there can
    look uncalled when it isn't -- coverage says so.
    """
    if not connector_id:
        return {"error": CONNECTOR_ID_REQUIRED}
    limit = max(1, min(int(limit or DEFAULT_ENTRY_LIMIT), MAX_QUERY_LIMIT))
    candidates: list[dict[str, Any]] = []
    after: str | None = None
    scanned = 0
    while scanned < _ENTRY_SCAN_BLOCKS:
        page = await graph_provider.get_documents_paginated(
            collection=_BLOCKS, limit=2000, sort_field="_key", after_key=after,
            filters={"orgId": org_id, "connectorId": connector_id}, raise_on_error=True,
        )
        if not page:
            break
        scanned += len(page)
        candidates += [b for b in page if (b.get("kind") or "").lower() in _ENTRY_KINDS]
        if len(page) < 2000:
            break
        after = page[-1].get("_key")
    scan_capped = scanned >= _ENTRY_SCAN_BLOCKS

    if repo:
        names = await repo_names(graph_provider, org_id, (b.get("recordGroupId") for b in candidates))
        candidates = [b for b in candidates if _repo_matches(repo, b.get("recordGroupId"), names)]
    access = RecordAccess(graph_provider, org_id, user_id)
    candidates = await _readable_only(access, candidates)
    if not include_tests:
        roles = await get_record_roles(graph_provider, org_id,
                                       {b.get("recordId") for b in candidates if b.get("recordId")})
        candidates = [b for b in candidates if roles.get(b.get("recordId")) != TEST_ROLE]

    called: set[str] = set()
    keys = [k for b in candidates if (k := _key_of(b))]
    for start in range(0, len(keys), _ENTRY_BATCH):
        rows = await graph_provider.get_neighbors_for_nodes_by_relationship_types(
            node_keys=keys[start:start + _ENTRY_BATCH], node_collection=_BLOCKS,
            relationship_types=[RecordRelations.CALLS.value], direction="inbound",
            limit=50000, raise_on_error=True,
        )
        called |= {r.get("anchorKey") for r in rows or []}
    uncalled = [b for b in candidates if _key_of(b) not in called]
    await attach_file_paths(graph_provider, org_id, uncalled)

    def reason(block: dict[str, Any]) -> str:
        if _ENTRY_NAMES.match(block.get("name") or ""):
            return "conventional name, no callers"
        if _ENTRY_FILES.search(block.get("filePath") or ""):
            return "entry-point file, no callers"
        return "no callers"

    ranked = sorted(uncalled, key=lambda b: (
        not _ENTRY_NAMES.match(b.get("name") or ""),
        not _ENTRY_FILES.search(b.get("filePath") or ""),
        b.get("filePath") or "", b.get("startLine") or 0,
    ))
    points = []
    for block in ranked[:limit]:
        ref = SymbolRef.from_block(block)
        ref.pop("kind", None)
        ref["reason"] = reason(block)
        points.append(ref)
    out: dict[str, Any] = {
        "connector_id": connector_id,
        **({"coverage": gap} if (gap := coverage_for(candidates)) else {}),
        "entry_points": points,
        "total": len(uncalled),
        "truncated": len(uncalled) > limit or scan_capped,
    }
    if scan_capped:
        out["note"] = (f"Only the first {_ENTRY_SCAN_BLOCKS} symbols were scanned; pass `repo` "
                       "to look at one repository.")
    return out

