"""What every CodeGraph tool shares: symbol addresses, repo identity and access.

The tools themselves live beside it -- ``neighbours`` (get_neighbour), ``read``
(read_code), ``path`` (find_symbol_path) and ``query`` (query_code_graph) --
kept out of the decorated class so they can be tested directly, without the
``@tool`` wrapper in the way.

A symbol is addressed by ``(file_path, qualified_name)``. Both halves are load
bearing: ``qualified_name`` (``method:BaseClient.send``) is unique only within a
file, so the path is what disambiguates it -- two indexed files can each hold
``function:main``.
"""
from __future__ import annotations

import logging
from collections import Counter
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames, RecordRelations
from app.modules.parsers.code_parser.lang_config import LANGUAGES

if TYPE_CHECKING:
    from collections.abc import Iterable

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

logger = logging.getLogger(__name__)

__all__ = [
    "CODE_RELATIONS",
    "CROSS_FILE_RELATIONS",
    "STRUCTURAL_RELATIONS",
    "TEST_ROLE",
    "AmbiguousRepoError",
    "RecordAccess",
    "SymbolRef",
    "attach_file_paths",
    "get_record_roles",
    "repo_names",
    "resolve_symbol",
]

TEST_ROLE = "test"

# Written at parse time, both endpoints inside one file. METHOD/CONTAINS are
# how a method names its class and a class names its members — without them a
# walk from a method cannot see the type it lives on.
STRUCTURAL_RELATIONS = [
    RecordRelations.CONTAINS.value,
    RecordRelations.DEFINES.value,
    RecordRelations.METHOD.value,
]

# Written by the edge-resolution pass once a repo is indexed: one symbol
# reaching another across files (calls, imports, heritage).
CROSS_FILE_RELATIONS = [
    RecordRelations.CALLS.value,
    RecordRelations.IMPORTS.value,
    RecordRelations.IMPORTS_FROM.value,
    RecordRelations.RE_EXPORTS.value,
    RecordRelations.EXPORTS.value,
    RecordRelations.INHERITS.value,
    RecordRelations.IMPLEMENTS.value,
    # The polymorphic hop. A call site typed to an interface binds to the
    # abstract method, so without this an inbound walk from a concrete
    # implementation finds no callers at all.
    RecordRelations.OVERRIDES.value,
]

# Heritage subset of CROSS_FILE — what a type is built on. Named for callers
# that want only that slice; `get_neighbour` defaults to CODE_RELATIONS below.
HERITAGE_RELATIONS = frozenset({
    RecordRelations.INHERITS.value,
    RecordRelations.IMPLEMENTS.value,
    RecordRelations.OVERRIDES.value,
})

# Everything the code graph emits. `get_neighbour` and `find_symbol_path`
# default to this full set so one hop from a method returns its class
# (METHOD/CONTAINS) and its callers/callees (CALLS), and the next hop on that
# class returns heritage and usages — no special-case path for overrides.
CODE_RELATIONS = [*STRUCTURAL_RELATIONS, *CROSS_FILE_RELATIONS]

# Degree is counted for at most this many candidates, over at most this many
# edge rows. Both caps are reported rather than applied silently: a truncated
# count ranks the wrong symbol first, which is worse than no ranking at all.
_DEGREE_CANDIDATES = 400
_DEGREE_ROW_LIMIT = 50000

_BLOCKS = CollectionNames.BLOCKS.value
_RECORDS = CollectionNames.RECORDS.value
_CODE_FILES = CollectionNames.CODE_FILES.value
_RECORD_GROUPS = CollectionNames.RECORD_GROUPS.value


# Languages the parser extracts cross-file facts for (calls and imports). Every
# other language is indexed for containment only, so an empty walk there says
# nothing about the code -- and without saying so, it reads as "no callers".
EDGE_LANGUAGES = frozenset(
    name for name, cfg in LANGUAGES.items() if cfg.call_types and cfg.import_handler_name
)
# Calls only, matched by name -- same file first, then a repo-unique name --
# and always INFERRED; no import or heritage edges (P-3).
NAME_ONLY_LANGUAGES = frozenset(
    name for name, cfg in LANGUAGES.items() if cfg.call_types and not cfg.import_handler_name
)


def coverage_for(blocks: "Iterable[dict[str, Any]]") -> dict[str, Any] | None:
    """The coverage gap for these blocks' languages, or None when there is none."""
    languages = {(b.get("language") or "").lower() for b in blocks} - {""} - EDGE_LANGUAGES
    if not languages:
        return None
    name_only = sorted(languages & NAME_ONLY_LANGUAGES)
    missing = sorted(languages - NAME_ONLY_LANGUAGES)
    parts = []
    if name_only:
        parts.append(
            f"For {', '.join(name_only)}, CALLS edges are matched by name only "
            "(confidence INFERRED) and there are no IMPORTS or heritage edges."
        )
    if missing:
        parts.append(
            f"No CALLS, IMPORTS or heritage edges are extracted for {', '.join(missing)}; "
            "only containment is indexed."
        )
    parts.append("A missing caller or path here is not evidence -- confirm with search "
                 "and read_code.")
    return {
        "cross_file_edges": False,
        "languages": sorted(languages),
        **({"name_matched_calls": name_only} if name_only else {}),
        "note": " ".join(parts),
    }


class AmbiguousRepoError(Exception):
    """A path names files in several repos of one connector, and no `repo` picked one.

    One GitHub or GitLab connector syncs many repos, and paths are
    repo-relative, so `backend/app/main.py` can be two unrelated files. Merging
    them, or answering from whichever came first, is a wrong answer that looks
    right; naming the repos lets the caller choose.
    """

    def __init__(self, file_path: str, repos: list[str]) -> None:
        self.repos = repos
        super().__init__(
            f"{file_path!r} exists in {len(repos)} repositories in this connector: "
            f"{', '.join(repos)}. Call again with `repo` set to one of them."
        )


class SymbolRef(dict):
    """A symbol as the model addresses it, plus what it needs to read one."""

    @staticmethod
    def from_block(block: dict[str, Any]) -> "SymbolRef":
        ref = SymbolRef(
            file_path=block.get("filePath"),
            qualified_name=block.get("qualifiedName"),
            kind=block.get("kind"),
        )
        if block.get("repo"):
            ref["repo"] = block["repo"]
        if block.get("startLine") is not None:
            ref["start_line"] = block.get("startLine")
        if block.get("endLine") is not None:
            ref["end_line"] = block.get("endLine")
        return ref


async def resolve_symbol(
    graph_provider: IGraphDBProvider,
    org_id: str,
    file_path: str,
    qualified_name: str,
    connector_id: str,
    *,
    repo: str | None = None,
    access: "RecordAccess | None" = None,
) -> dict[str, Any] | None:
    """Find the block node for ``(file_path, qualified_name)`` in one repo connector.

    Paths are repo-relative, so `(file_path, qualified_name)` is only unique within a
    connector -- two indexed repos can each hold `src/main.py#function:main`.

    Identity is case-sensitive because `Foo` and `foo` are different symbols in
    every language this indexes, but the lookup is not: a model retyping an
    address rather than copying it should not silently get "no such symbol".
    The exact match wins whenever there is one, so a file holding both spellings
    still resolves each correctly.

    A storage failure raises: returning None would tell the caller the symbol
    does not exist.
    """
    record_ids = await _records_for_path(graph_provider, org_id, file_path)
    if access is not None:
        record_ids = await _choose_records(
            graph_provider, org_id, record_ids, connector_id,
            file_path=file_path, repo=repo, access=access, ambiguous_ok=True,
        )
    if not record_ids:
        return None
    found: list[dict[str, Any]] = []
    for record_id in record_ids:
        rows = await graph_provider.get_nodes_by_filters(
            collection=_BLOCKS,
            filters={
                "orgId": org_id,
                "recordId": record_id,
                "qualifiedName": qualified_name,
                "connectorId": connector_id,
            },
            raise_on_error=True,
        )
        found.extend(rows[:1])
    if not found:
        found = [hit for hit in [await _resolve_case_insensitive(
            graph_provider, org_id, record_ids, file_path, qualified_name, connector_id
        )] if hit]
    if not found:
        return None
    # Two repos can each hold the path; only the ones holding the symbol count.
    await _raise_if_several_repos(graph_provider, org_id, file_path, found)
    block = _with_file_path(found[0], file_path)
    await _attach_repos(graph_provider, org_id, [block])
    return block


async def _records_for_path(
    graph_provider: IGraphDBProvider, org_id: str, file_path: str
) -> list[str]:
    """Record ids for a repo-relative path.

    The path is a property of the file, not of its symbols, so it is stored once
    on ``codeFiles`` (keyed by record id) rather than on every block. Normally one
    id; more than one means two connectors indexed the same path, and the caller
    narrows by connector.
    """
    rows = await graph_provider.get_nodes_by_filters(
        collection=_CODE_FILES,
        filters={"orgId": org_id, "filePath": file_path},
        return_fields=["_key"],
        raise_on_error=True,
    )
    return [key for row in rows or [] if (key := row.get("_key") or row.get("id"))]


async def _choose_records(
    graph_provider: IGraphDBProvider,
    org_id: str,
    record_ids: list[str],
    connector_id: str,
    *,
    file_path: str,
    repo: str | None,
    access: "RecordAccess",
    ambiguous_ok: bool = False,
) -> list[str]:
    """The records for one path that are in this connector, readable, and in one repo.

    A repo the caller cannot read is never named: listing it in an ambiguity
    error would say the path exists there. With ``ambiguous_ok`` the records of
    every remaining repo come back, for a caller that narrows further (by
    symbol) before deciding.
    """
    if not record_ids:
        return []
    rows = await graph_provider.get_nodes_by_field_in(
        collection=_RECORDS, field_name="_key", field_values=record_ids,
        return_fields=["_key", "orgId", "connectorId", "recordGroupId"], raise_on_error=True,
    )
    rows = [row for row in (_unwrap(r) for r in rows or [])
            if row.get("orgId") == org_id and row.get("connectorId") == connector_id]
    readable = await access.readable(row.get("_key") or row.get("id") for row in rows)
    rows = [row for row in rows if (row.get("_key") or row.get("id")) in readable]
    if repo:
        names = await repo_names(graph_provider, org_id, {row.get("recordGroupId") for row in rows})
        rows = [row for row in rows
                if _repo_matches(repo, row.get("recordGroupId"), names)]
    keys = [row.get("_key") or row.get("id") for row in rows]
    if not ambiguous_ok:
        await _raise_if_several_repos(graph_provider, org_id, file_path, rows)
    return keys


async def _raise_if_several_repos(
    graph_provider: IGraphDBProvider, org_id: str, file_path: str, rows: list[dict[str, Any]],
) -> None:
    groups = {row.get("recordGroupId") for row in rows}
    if len(groups) < 2:
        return
    names = await repo_names(graph_provider, org_id, groups)
    raise AmbiguousRepoError(file_path, sorted(names.get(g) or str(g) for g in groups))


def _repo_matches(repo: str, group_id: str | None, names: dict[str, str]) -> bool:
    """`repo` as the caller wrote it: the group id, `owner/name`, or just `name`."""
    if not group_id:
        return False
    if repo == group_id:
        return True
    name = (names.get(group_id) or "").casefold()
    wanted = repo.casefold().strip("/")
    return bool(name) and (name == wanted or name.endswith("/" + wanted))


async def repo_names(
    graph_provider: IGraphDBProvider, org_id: str, group_ids: "Iterable[str | None]",
) -> dict[str, str]:
    """Repo name per record group, e.g. ``acme/shop``.

    A code-host connector files code under a child group named "Code
    repository" whose parent is the repo (`owner/name`); that parent's name is
    the one a person recognises. A group with no parent (an uploaded folder)
    names itself.
    """
    ids = sorted({g for g in group_ids if g})
    if not ids:
        return {}
    groups = [
        _unwrap(row) for row in await graph_provider.get_nodes_by_field_in(
            collection=_RECORD_GROUPS, field_name="_key", field_values=ids,
            return_fields=["_key", "orgId", "connectorId", "groupName", "parentExternalGroupId"],
            raise_on_error=True,
        ) or []
    ]
    groups = [g for g in groups if g.get("orgId") == org_id]
    parent_ids = sorted({g["parentExternalGroupId"] for g in groups if g.get("parentExternalGroupId")})
    parents: dict[tuple[str | None, str], str] = {}
    if parent_ids:
        for row in await graph_provider.get_nodes_by_field_in(
            collection=_RECORD_GROUPS, field_name="externalGroupId", field_values=parent_ids,
            return_fields=["orgId", "connectorId", "externalGroupId", "groupName"],
            raise_on_error=True,
        ) or []:
            parent = _unwrap(row)
            if parent.get("orgId") == org_id and parent.get("groupName"):
                parents[(parent.get("connectorId"), parent["externalGroupId"])] = parent["groupName"]
    out: dict[str, str] = {}
    for g in groups:
        key = g.get("_key") or g.get("id")
        parent = parents.get((g.get("connectorId"), g.get("parentExternalGroupId") or ""))
        name = parent or g.get("groupName")
        if key and name:
            out[key] = name
    return out


async def _attach_repos(
    graph_provider: IGraphDBProvider, org_id: str, blocks: "Iterable[dict[str, Any]]",
) -> None:
    blocks = list(blocks)
    names = await repo_names(graph_provider, org_id, (b.get("recordGroupId") for b in blocks))
    for block in blocks:
        group = block.get("recordGroupId")
        if group:
            block["repo"] = names.get(group) or group


def _with_file_path(block: dict[str, Any], file_path: str | None) -> dict[str, Any]:
    """Attach the owning record's path so callers can address the block."""
    block["filePath"] = file_path
    return block


async def _resolve_case_insensitive(
    graph_provider: IGraphDBProvider,
    org_id: str,
    record_ids: list[str],
    file_path: str,
    qualified_name: str,
    connector_id: str,
) -> dict[str, Any] | None:
    """Fall back to a casefolded match among that file's blocks.

    Scoped to the one file, so this reads a handful of rows rather than scanning.
    The kind prefix is optional (`Response.close` names `method:Response.close`),
    and so is a leading `path::` the caller may have copied: an agent naming a
    symbol in a file it knows got an empty result, read as "nothing calls this".
    Ambiguous when the file holds several matching spellings -- there is no right
    answer then, so it resolves nothing rather than guess.
    """
    wanted = qualified_name.rpartition("::")[2].casefold()
    rows: list[dict[str, Any]] = []
    for record_id in record_ids:
        rows.extend(await graph_provider.get_nodes_by_filters(
            collection=_BLOCKS,
            filters={"orgId": org_id, "recordId": record_id, "connectorId": connector_id},
            raise_on_error=True,
        ) or [])

    def names(row: dict[str, Any]) -> set[str]:
        stored = (row.get("qualifiedName") or "").casefold()
        return {stored, stored.partition(":")[2]} if ":" in stored else {stored}

    matches = [r for r in rows if wanted in names(r)]
    if len(matches) == 1:
        return _with_file_path(matches[0], file_path)
    if matches:
        logger.debug("Ambiguous case-insensitive match for %s#%s", file_path, qualified_name)
    return None


async def _user_can_read(
    graph_provider: IGraphDBProvider, user_id: str, org_id: str, record_id: str | None
) -> bool:
    """Gate every result on the caller's access to the owning record.

    A denial is reported by the caller as an empty result rather than an error:
    telling an agent that a record it cannot read exists is itself a leak.

    ``_RecordResolver.resolve`` (app/utils/fetch_full_record.py) runs the same
    check, but as a method over that resolver's held state, fused with the
    document fetch -- there is no free function there to delegate to.
    """
    if not record_id:
        return False
    if not user_id:
        return False
    try:
        access = await graph_provider.check_record_access_with_details(
            user_id=user_id, org_id=org_id, record_id=record_id
        )
    except Exception as exc:
        logger.warning("Access check failed for record %s: %s", record_id, exc)
        return False
    return bool(access)


async def _readable_only(
    access: RecordAccess, blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drop blocks whose owning record the caller cannot read, keeping order."""
    allowed = await access.readable(b.get("recordId") for b in blocks)
    return [b for b in blocks if b.get("recordId") in allowed]


async def _readable_blocks(
    graph_provider: IGraphDBProvider,
    org_id: str,
    access: RecordAccess,
    keys: list[str],
    connector_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Load block nodes by key, dropping any whose record the user cannot read.

    ``connector_id`` re-applies the repo scope on nodes reached by traversal.

    A failed load raises rather than returning ``{}``: empty here reads as
    "no neighbours" to every caller.
    """
    if not keys:
        return {}
    rows = await graph_provider.get_nodes_by_field_in(
        collection=_BLOCKS, field_name="_key", field_values=keys, raise_on_error=True
    )

    candidates: list[dict[str, Any]] = []
    for row in rows or []:
        if row.get("orgId") != org_id:
            continue
        if connector_id is not None and row.get("connectorId") != connector_id:
            continue
        candidates.append(row)

    kept = await _readable_only(access, candidates)
    out = {(row.get("_key") or row.get("id")): row for row in kept}
    await attach_file_paths(graph_provider, org_id, out.values())
    return out


async def _readable_keys(
    graph_provider: IGraphDBProvider, org_id: str, access: RecordAccess, keys: "Iterable[str]",
) -> set[str]:
    """The keys among ``keys`` of blocks in this org whose file the caller may read.

    The one check a traversal applies at every hop (S-7): what is reached
    through a file the caller cannot read, and what is counted, must not
    depend on that file. Loads only key, record and org.
    """
    wanted = sorted({k for k in keys if k})
    if not wanted:
        return set()
    rows = [
        _unwrap(row) for row in await graph_provider.get_nodes_by_field_in(
            collection=_BLOCKS, field_name="_key", field_values=wanted,
            return_fields=["_key", "recordId", "orgId"], raise_on_error=True,
        ) or []
    ]
    rows = [r for r in rows if r.get("orgId") == org_id]
    readable = await access.readable(r.get("recordId") for r in rows)
    return {key for r in rows
            if r.get("recordId") in readable and (key := r.get("_key") or r.get("id"))}


async def attach_file_paths(
    graph_provider: IGraphDBProvider, org_id: str, blocks: "Iterable[dict[str, Any]]"
) -> None:
    """Fill in each block's ``filePath`` from its owning record.

    The path is stored once per file on ``codeFiles``, so it is joined here
    rather than copied onto every block. Every tool reaches its blocks through
    this or ``resolve_symbol``, which is what lets the rest of the code-graph
    code keep reading ``block["filePath"]`` unchanged.
    """
    blocks = list(blocks)
    record_ids = sorted({b.get("recordId") for b in blocks if b.get("recordId")})
    if not record_ids:
        return
    # Raises on failure: a block without its path cannot be addressed or read.
    file_info = await graph_provider.get_file_paths_for_records(
        org_id=org_id, record_ids=record_ids, raise_on_error=True
    )
    for block in blocks:
        info = file_info.get(block.get("recordId"))
        block["filePath"] = info.file_path if info else None
    await _attach_repos(graph_provider, org_id, blocks)


def _key_of(block: dict) -> str:
    return block.get("_key") or block.get("id") or ""


async def _degrees(
    graph_provider: IGraphDBProvider, blocks: list[dict], relations: list[str],
    org_id: str, access: RecordAccess,
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
    rows = list(rows or [])
    # Only edges to what the caller can read: a count over hidden files says
    # how much is hidden there (S-7).
    readable_blocks = await _readable_keys(
        graph_provider, org_id, access,
        (r.get("key") for r in rows if r.get("collection") == _BLOCKS),
    )
    readable_records = await access.readable(
        r.get("key") for r in rows if r.get("collection") == _RECORDS
    )
    counts: Counter = Counter()
    for row in rows:
        anchor = row.get("anchorKey")
        key = row.get("key")
        if anchor and (key in readable_blocks or key in readable_records):
            counts[anchor] += 1
    capped = len(rows) >= _DEGREE_ROW_LIMIT or len(blocks) > _DEGREE_CANDIDATES
    return dict(counts), capped


async def path_for_record(
    graph_provider: IGraphDBProvider, org_id: str, record_id: str
) -> str | None:
    """The repo-relative path of a code record, for callers holding only its id.

    A knowledge search hands back a ``Record ID`` and a basename; every tool
    here addresses files by repo-relative path. Resolving here means the id is
    swapped for a path before any lookup, so connector scoping and the ACL
    check run on the path exactly as they do for a caller who typed one.
    """
    try:
        file_info = await graph_provider.get_file_paths_for_records(
            org_id=org_id, record_ids=[record_id]
        )
    except Exception as exc:
        logger.warning("Path lookup failed for record %s: %s", record_id, exc)
        return None
    info = (file_info or {}).get(record_id)
    return info.file_path if info else None


def _unwrap(row: dict) -> dict:
    """Providers return either the node itself or {'b': node}."""
    if isinstance(row, dict) and len(row) <= 2 and ("b" in row or "node" in row):
        inner = row.get("b") or row.get("node")
        if isinstance(inner, dict):
            return inner
    return row


async def get_record_roles(
    graph_provider: IGraphDBProvider, org_id: str, record_ids: set[str] | list[str]
) -> dict[str, str]:
    """``fileRole`` per record, read from ``codeFiles``.

    A failed lookup raises: ``{}`` would let every test file through
    ``include_tests=False``.
    """
    if not record_ids:
        return {}
    ids = sorted(record_ids)
    rows = await graph_provider.get_nodes_by_field_in(
        collection=_CODE_FILES,
        field_name="_key",
        field_values=ids,
        return_fields=["_key", "orgId", "fileRole"],
        raise_on_error=True,
    )
    out: dict[str, str] = {}
    for raw in rows or []:
        row = _unwrap(raw)
        # `get_nodes_by_field_in` takes no filters, so the org scope every other
        # lookup here applies at the query is re-applied on the rows.
        if row.get("orgId") != org_id:
            continue
        key = row.get("_key") or row.get("id")
        role = row.get("fileRole")
        if key and role:
            out[key] = role
    return out


class RecordAccess:
    """Which records the caller may read, decided per record id and cached.

    Keyed by record id on purpose. The bulk map behind search
    (``get_accessible_virtual_record_ids``) keeps one record per content
    identity, so of two byte-identical files -- a vendored copy, a fork, the
    same helper in two services -- every copy but one vanished from the tools
    while results still said ``truncated: false``.

    A failed check raises: an empty answer would read as "nothing here".
    """

    def __init__(self, graph_provider: IGraphDBProvider, org_id: str, user_id: str) -> None:
        self._graph_provider = graph_provider
        self._org_id = org_id
        self._user_id = user_id
        self._verdicts: dict[str, bool] = {}

    async def readable(self, record_ids: "Iterable[str | None]") -> set[str]:
        wanted = {rid for rid in record_ids if rid}
        if not self._user_id:
            return set()
        unknown = sorted(rid for rid in wanted if rid not in self._verdicts)
        if unknown:
            allowed = await self._graph_provider.filter_accessible_record_ids(
                unknown, self._user_id, self._org_id,
            )
            for rid in unknown:
                self._verdicts[rid] = rid in (allowed or ())
        return {rid for rid in wanted if self._verdicts.get(rid)}


async def _record_in_connector(
    graph_provider: IGraphDBProvider, org_id: str, record_ids: list[str], connector_id: str
) -> str | None:
    """Pick the record from *record_ids* that belongs to this connector."""
    if not record_ids:
        return None
    for record_id in record_ids:
        rows = await graph_provider.get_nodes_by_filters(
            collection=_BLOCKS,
            filters={"orgId": org_id, "recordId": record_id, "connectorId": connector_id},
            raise_on_error=True,
        )
        if rows:
            return record_id
    return None
