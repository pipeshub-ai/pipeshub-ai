"""impact_of_diff: what a patch changes, and what that breaks.

A unified diff names lines; the graph names symbols. Each changed line on the old side -- the indexed commit -- is
mapped to the innermost symbol around it, and each changed symbol is walked with ``impact``. The results merge by
file, so one call answers "what does this PR break" without a walk per symbol from the agent.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from app.modules.parsers.code_parser.models import FILLER_KINDS

from .diff_parse import FileDiff, parse_unified_diff
from .neighbours import DEFAULT_IMPACT_DEPTH, impact_impl
from .ops import _BLOCKS, RecordAccess, _choose_records, _records_for_path, _unwrap
from .results import touched

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

    Walk = Callable[..., Awaitable[dict[str, Any]]]

__all__ = ["impact_of_diff_impl"]

# Changed symbols walked per call; the rest are counted. Each is one impact walk.
_MAX_SYMBOLS = 25
# Direct callers checked for a test that reaches them; each check is one more walk.
_MAX_UNTESTED_CHECKS = 10
_WALKS_AT_ONCE = 4
# Filler a change can sit in without changing what depends on the file: its imports, comments and header.
_NOT_READ_BY_DEPENDENTS = FILLER_KINDS - {"statements"}

_NOTE = ("Changed lines are matched against the indexed commit; when the patch's base is "
         "another commit, line numbers can drift onto a neighbouring symbol.")


async def impact_of_diff_impl(
    *,
    graph_provider: IGraphDBProvider,
    connector_id: str,
    org_id: str,
    user_id: str,
    patch: str,
    depth: int = DEFAULT_IMPACT_DEPTH,
    include_tests: bool = False,
    repo: str | None = None,
) -> dict[str, Any]:
    """The symbols *patch* changes, the files and tests their change reaches, and the direct callers no test reaches.

    ``changed`` holds one row per changed symbol (innermost around each changed line), or ``whole_file`` for a
    changed file whose changes fall outside every symbol. A file the graph does not hold -- or the caller cannot
    read; the two read the same -- is listed in ``not_indexed``; a new file in ``added_files``.
    """
    files = parse_unified_diff(patch or "")
    if not files:
        return {"error": "No file changes found: pass a unified diff (`git diff` output), with ---/+++ headers."}
    access = RecordAccess(graph_provider, org_id, user_id)
    changed: list[dict[str, Any]] = []
    blocks_seen: list[dict[str, Any]] = []
    not_indexed: list[str] = []
    mode_only: list[str] = []
    for diff in files:
        if diff.old_path is None:
            continue
        if diff.status == "modified" and not diff.hunks and not diff.binary:
            mode_only.append(diff.old_path)  # a chmod: no line changed, so no symbol did
            continue
        found = await _file_blocks(graph_provider, org_id, connector_id, diff.old_path, repo, access)
        if found is None:
            not_indexed.append(diff.old_path)
            continue
        path, blocks = found
        blocks_seen += blocks
        changed += _changed_symbols(diff, path, blocks)
    walked, omitted = changed[:_MAX_SYMBOLS], len(changed) - _MAX_SYMBOLS

    gate = asyncio.Semaphore(_WALKS_AT_ONCE)

    async def walk(row: dict[str, Any], **kwargs: object) -> dict[str, Any]:
        async with gate:
            return await impact_impl(graph_provider=graph_provider, connector_id=connector_id, org_id=org_id,
                                     user_id=user_id, file_path=row["file_path"],
                                     qualified_name=row.get("qualified_name"), repo=repo, **kwargs)

    walks = await asyncio.gather(*(walk(r, depth=depth, include_tests=include_tests) for r in walked))
    untested, unchecked, failed_direct = await _untested_callers(walked, walk)
    out: dict[str, Any] = {
        "changed": walked,
        "added_files": [d.path for d in files if d.old_path is None],
        "not_indexed": not_indexed,
        **({"mode_only": mode_only} if mode_only else {}),
        "impacted_files": _merge(w.get("impacted_files") for w in walks),
        "impacted_tests": _merge(w.get("impacted_tests") for w in walks),
        "untested_callers": untested,
        "note": _NOTE,
    }
    # A walk can fail (the symbol was re-indexed away since its blocks were read): name it, never drop it.
    failed = list({(f["file_path"], f["qualified_name"]): f
                   for f in [*_failures(walked, walks), *failed_direct]}.values())
    if failed:
        out.update(failed_walks=failed, truncated=True)
    if unchecked:
        out.update(untested_unchecked=unchecked, truncated=True)
    if omitted > 0:
        out.update(changed_omitted=omitted, truncated=True)
    if any(w.get("truncated") for w in walks):
        out["truncated"] = True
    return touched(out, blocks_seen)


async def _file_blocks(
    graph_provider: IGraphDBProvider, org_id: str, connector_id: str, file_path: str, repo: str | None,
    access: RecordAccess,
) -> tuple[str, list[dict[str, Any]]] | None:
    """The indexed path and its blocks (symbols and the filler between them), or None when no readable file in this
    connector has the path."""
    async def choose(keys: list[str]) -> list[str]:
        return await _choose_records(graph_provider, org_id, keys, connector_id, file_path=file_path, repo=repo,
                                     access=access)

    record_ids, path = await _records_for_path(graph_provider, org_id, file_path, choose=choose)
    if not record_ids:
        return None
    rows = await graph_provider.get_nodes_by_field_in(
        collection=_BLOCKS, field_name="recordId", field_values=record_ids, raise_on_error=True,
        return_fields=["qualifiedName", "kind", "startLine", "endLine", "recordGroupId"],
    ) or []
    # The records are already this org's, this connector's and readable (`_choose_records`), so are their blocks:
    # valid while record ids are globally unique.
    blocks = [b for b in (_unwrap(r) for r in rows)
              if b.get("qualifiedName")
              and isinstance(b.get("startLine"), int) and isinstance(b.get("endLine"), int)]
    return path, blocks


def _changed_symbols(diff: FileDiff, path: str, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The innermost symbol around each removed line and each insertion point, in line order, and the whole file
    when a change is module-level code (or nothing else changed).

    An insertion after line A is inside a block that spans A, its last line included: an append to a Python
    function lands after its last line, and nothing in the diff says whether it stayed inside."""
    def innermost(line: int) -> dict[str, Any] | None:
        # Spans tile a file byte for byte, so a member ends on the next member's first line: of the blocks
        # holding a line, the one that starts latest is the innermost and owns that shared line.
        around = [b for b in blocks if b["startLine"] <= line <= b["endLine"]]
        return max(around, key=lambda b: (b["startLine"], -(b["endLine"] - b["startLine"])), default=None)

    hits: dict[str, dict[str, Any]] = {}
    module_level = False
    for line in sorted({*diff.removed, *diff.inserted_after}):
        block = innermost(line)
        kind = (block or {}).get("kind")
        if block is not None and kind not in FILLER_KINDS:
            hits.setdefault(block["qualifiedName"], block)
        elif kind not in _NOT_READ_BY_DEPENDENTS:
            module_level = True  # a module-level constant or statement: whatever uses the file can read it
    rows = [{"file_path": path, "qualified_name": b["qualifiedName"], "kind": b.get("kind"),
             "lines": f"{b['startLine']}-{b['endLine']}"}
            for b in sorted(hits.values(), key=lambda b: b["startLine"])]
    if module_level or not rows:
        rows.append({"file_path": path, "whole_file": True})
    return rows


def _merge(groups: Iterable[list[dict[str, Any]] | None]) -> list[dict[str, Any]]:
    """Per-symbol ``impacted_files`` lists as one: a file reached from two changes is listed once, at its nearest."""
    by_file: dict[str, dict[str, Any]] = {}
    for rows in groups:
        for row in rows or ():
            entry = by_file.setdefault(row["file_path"], {**row, "symbols": []})
            entry["nearest_hop"] = min(entry["nearest_hop"], row["nearest_hop"])
            entry["symbols"] += [s for s in row.get("symbols", ()) if s not in entry["symbols"]]
            if row.get("built_on"):
                entry["built_on"] = list(dict.fromkeys([*entry.get("built_on", ()), *row["built_on"]]))
            if row.get("via_override"):
                entry["via_override"] = {**row["via_override"], **entry.get("via_override", {})}
    return sorted(by_file.values(), key=lambda f: (f["nearest_hop"], f["file_path"]))


def _failures(rows: list[dict[str, Any]], walks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The walks that failed, named by the symbol they started from."""
    return [{"file_path": r["file_path"], "qualified_name": r.get("qualified_name"), "error": w["error"]}
            for r, w in zip(rows, walks) if w.get("error")]


async def _untested_callers(
    changed: list[dict[str, Any]], walk: Walk,
) -> tuple[list[dict[str, str]], int, list[dict[str, Any]]]:
    """Direct callers of a changed symbol that no test reaches within two hops (the change's unguarded edges), how
    many callers were not checked (past the cap, or their check failed), and the caller lookups that failed (their
    callers are unknown)."""
    symbols = [r for r in changed if r.get("qualified_name")]
    direct = await asyncio.gather(*(walk(r, depth=1, include_tests=False, edge_types=["CALLS"]) for r in symbols))
    callers = list(dict.fromkeys((f["file_path"], s) for w in direct for f in w.get("impacted_files") or ()
                                 for s in f.get("symbols", ())))
    checked = callers[:_MAX_UNTESTED_CHECKS]
    reached = await asyncio.gather(*(walk({"file_path": p, "qualified_name": q}, depth=2, include_tests=False)
                                     for p, q in checked))
    untested = [{"file_path": p, "qualified_name": q} for (p, q), w in zip(checked, reached)
                if not w.get("error") and not w.get("impacted_tests")]
    return untested, len(callers) - len(checked) + sum(1 for w in reached if w.get("error")), _failures(symbols, direct)
