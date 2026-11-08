"""history: the pull requests that changed a file, their people and the tickets they name.

A PR reaches a code file through MODIFIES (written where the PR's file list is read) and a ticket through
the PR's LINKED_TO (its title and body name it). Everything listed passes the caller's access check, and a count
covers only what is listed: a hidden PR or ticket changes nothing in the answer.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames, RecordRelations
from app.modules.artifacts.reader import record_artifact_reader

from .ops import (
    _RECORDS,
    RecordAccess,
    _choose_records,
    _records_for_path,
    nodes_in_org,
)

if TYPE_CHECKING:
    from app.modules.artifacts.reader import RecordArtifactReader
    from app.modules.transformers.blob_storage import BlobStorage
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = ["DEFAULT_HISTORY_LIMIT", "history_impl"]

logger = logging.getLogger(__name__)

DEFAULT_HISTORY_LIMIT = 20
_PRS = CollectionNames.PULLREQUESTS.value
_ROW_LIMIT = 5000
_READS_AT_ONCE = 8
_NO_PRS = ("No indexed pull request changed this file. A pull request is linked to the files it changes when it "
           "is indexed, so one that never was is not listed.")
_FILE_LEVEL = ("Pull requests are found per file: `touches_symbol` is not known here, so a PR listed here changed "
               "the file, not necessarily this symbol.")
_HEURISTIC = ("Pull requests are found per file. `touches_symbol` is a heuristic: true when the PR's diff for this "
              "file names the symbol in a changed line or hunk header, null when the PR's stored diff has no change "
              "for the file (indexed before file changes were stored).")


async def history_impl(
    *,
    graph_provider: IGraphDBProvider,
    connector_id: str,
    org_id: str,
    user_id: str,
    file_path: str,
    qualified_name: str | None = None,
    limit: int = DEFAULT_HISTORY_LIMIT,
    repo: str | None = None,
    blob_store: BlobStorage | None = None,
) -> dict[str, Any]:
    """The readable PRs that changed *file_path*, merged newest first and open ones last, each with its author,
    reviewers, merger and the readable tickets it names. *qualified_name* is answered at file level (a note says so).
    """
    access = RecordAccess(graph_provider, org_id, user_id)

    async def choose(keys: list[str]) -> list[str]:
        return await _choose_records(graph_provider, org_id, keys, connector_id, file_path=file_path, repo=repo,
                                     access=access)

    record_ids, path = await _records_for_path(graph_provider, org_id, file_path, choose=choose)
    if not record_ids:
        return {"error": f"No readable indexed file {file_path!r} in this repository."}
    pr_ids, capped = await _linked(graph_provider, record_ids, RecordRelations.MODIFIES.value, "inbound")
    pr_ids = sorted(await access.readable(pr_ids))
    out: dict[str, Any] = {"file_path": path, "connector_id": connector_id}
    if not pr_ids:
        return {**out, "pull_requests": [], "total": 0, "note": _NO_PRS}

    records = {r["_key"]: r for r in await nodes_in_org(
        graph_provider, org_id, _RECORDS, pr_ids,
        ["_key", "recordName", "webUrl", "connectorName", "virtualRecordId"])}
    details = {r["_key"]: r for r in await nodes_in_org(
        graph_provider, org_id, _PRS, pr_ids,
        ["_key", "status", "creatorName", "reviewName", "mergedBy", "mergedAtTimestamp"])}
    rows = [_row(records[p], details.get(p, {}), symbol=qualified_name) for p in pr_ids if p in records]
    # Merged newest first, then the never-merged; the title breaks ties so the order is the same every call.
    rows.sort(key=lambda r: (r["merged_at"] is None, -(r["merged_at"] or 0), r["title"]))
    out.update(pull_requests=rows[:limit], total=len(rows))
    # Tickets and diffs are read for the returned page only, not for every PR that reaches the file.
    tickets_of = await _tickets(graph_provider, org_id, access, [r["_key"] for r in out["pull_requests"]])
    for row in out["pull_requests"]:
        row["tickets"] = tickets_of.get(row["_key"], [])
    if len(rows) > limit:
        out["truncated"] = True
    notes = []
    if capped:
        # The lookup stopped at the cap, so `total` and the order are over a sample of the file's PRs.
        out["truncated"] = True
        notes.append(f"More than {_ROW_LIMIT} pull request links reach this file; only the first {_ROW_LIMIT} "
                     "were read, so this list and its total are a sample.")
    if qualified_name:
        if blob_store is None:
            notes.append(_FILE_LEVEL)
        else:
            reader = record_artifact_reader(blob_store)
            gate = asyncio.Semaphore(_READS_AT_ONCE)

            async def touch(row: dict[str, Any]) -> None:
                async with gate:
                    row["touches_symbol"] = await _touches(reader, records[row["_key"]], path, qualified_name,
                                                           org_id)

            await asyncio.gather(*(touch(row) for row in out["pull_requests"]))
            notes.append(_HEURISTIC)
    if notes:
        out["note"] = " ".join(notes)
    for row in out["pull_requests"]:
        row.pop("_key", None)
    return out


async def _touches(reader: RecordArtifactReader, record: dict[str, Any], path: str, qualified_name: str,
                   org_id: str) -> bool | None:
    """Whether the PR's diff for *path* names the symbol in a changed line or a hunk header; None when its
    stored blocks hold no change for the file (or cannot be read)."""
    vrid = record.get("virtualRecordId")
    try:
        blob = await reader.read_record(vrid, org_id) if vrid else None
    except Exception:
        logger.warning("Could not read pull request %s for its diff", record.get("_key"), exc_info=True)
        return None
    container = (blob or {}).get("block_containers") or {}
    diffs = [text for g in container.get("block_groups") or [] if isinstance(g, dict)
             and (g.get("file_change_metadata") or {}).get("path") == path for text in _group_text(container, g)]
    if not diffs:
        return None
    # Lookarounds, not \b: a name that starts or ends with a non-word character (`$emit`, `empty?`) has no \b.
    name = re.compile(rf"(?<!\w){re.escape(_bare(qualified_name))}(?!\w)")
    changed = (line for text in diffs for line in text.splitlines()
               if line.startswith("@@") or (line[:1] in ("+", "-") and not line.startswith(("+++", "---"))))
    return any(name.search(line) for line in changed)


def _group_text(container: dict[str, Any], group: dict[str, Any]) -> list[str]:
    """The text of a block group: its own ``data`` as the connector wrote it, or, once indexing has split it,
    the blocks under it (through nested groups such as the diff's code group)."""
    if isinstance(group.get("data"), str):
        return [group["data"]]
    parents = {g.get("index"): g.get("parent_index") for g in container.get("block_groups") or []
               if isinstance(g, dict)}

    def under(index: object) -> bool:
        seen: set[object] = set()
        while index is not None and index not in seen:
            if index == group.get("index"):
                return True
            seen.add(index)
            index = parents.get(index)
        return False

    return [b["data"] for b in container.get("blocks") or []
            if isinstance(b, dict) and isinstance(b.get("data"), str) and under(b.get("parent_index"))]


def _bare(qualified_name: str) -> str:
    """``method:Client.fetch#2`` -> ``fetch``: what a diff line names."""
    return re.sub(r"#\d+$", "", qualified_name.rpartition(":")[2]).rpartition(".")[2]


async def _linked(graph_provider: IGraphDBProvider, keys: list[str], relation: str,
                  direction: str) -> tuple[list[str], bool]:
    """The records one *relation* hop away, and whether the row cap cut the lookup short."""
    rows = await graph_provider.get_neighbors_for_nodes_by_relationship_types(
        node_keys=keys, node_collection=_RECORDS, relationship_types=[relation], direction=direction,
        limit=_ROW_LIMIT, raise_on_error=True,
    ) or []
    return sorted({r["key"] for r in rows if r.get("collection") == _RECORDS and r.get("key")}), \
        len(rows) >= _ROW_LIMIT


async def _tickets(
    graph_provider: IGraphDBProvider, org_id: str, access: RecordAccess, pr_ids: list[str],
) -> dict[str, list[dict[str, Any]]]:
    """The readable records each PR names (LINKED_TO), as ``{title, url, type}``."""
    if not pr_ids:
        return {}
    rows = await graph_provider.get_neighbors_for_nodes_by_relationship_types(
        node_keys=pr_ids, node_collection=_RECORDS, relationship_types=[RecordRelations.LINKED_TO.value],
        direction="outbound", limit=_ROW_LIMIT, raise_on_error=True,
    ) or []
    links = [(r["anchorKey"], r["key"]) for r in rows if r.get("collection") == _RECORDS and r.get("key")]
    readable = await access.readable(key for _, key in links)
    named = {r["_key"]: r for r in await nodes_in_org(
        graph_provider, org_id, _RECORDS, readable, ["_key", "recordName", "webUrl", "recordType"])}
    out: dict[str, list[dict[str, Any]]] = {}
    for pr, key in links:
        if key in named:
            t = named[key]
            out.setdefault(pr, []).append({"title": t.get("recordName"), "url": t.get("webUrl"),
                                           "type": t.get("recordType")})
    return out


def _row(record: dict[str, Any], pr: dict[str, Any], *, symbol: str | None) -> dict[str, Any]:
    return {
        "_key": record["_key"],  # for the tickets and the symbol check; dropped before the result leaves
        "title": record.get("recordName"),
        "url": record.get("webUrl"),
        "status": pr.get("status"),
        "merged_at": pr.get("mergedAtTimestamp"),
        "author": pr.get("creatorName"),
        # Asked, not necessarily reviewed: GitHub lists requested reviewers, GitLab's MR `reviewers` are the
        # assigned ones, and who approved (`approved_by`) is not fetched.
        "requested_reviewers": list(pr.get("reviewName") or []),
        "merged_by": pr.get("mergedBy"),
        "tickets": [],  # filled for the returned page
        **({"touches_symbol": None} if symbol else {}),
    }
