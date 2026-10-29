"""read_code: a symbol's or a file's stored source, with line ranges."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.modules.artifacts.reader import record_artifact_reader
from app.modules.parsers.code_parser.models import FILLER_KINDS

from .ops import (
    _RECORDS,
    TEST_ROLE,
    RecordAccess,
    SymbolRef,
    _choose_records,
    _records_for_path,
    get_record_roles,
    repo_names,
    resolve_symbol,
)

if TYPE_CHECKING:
    from app.modules.transformers.blob_storage import BlobStorage
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

logger = logging.getLogger(__name__)

__all__ = ["DEFAULT_MAX_LINES", "MAX_READ_LINES", "outline_impl", "read_code_impl"]

# Default ceiling on a whole-file read. Enough for ~95% of files outright, and
# it is the hub files an architecture question lands on that overrun it -- which
# is exactly where an uncapped read costs thousands of tokens of context.
DEFAULT_MAX_LINES = 600
# The most one read returns however much is asked for: a model that asks for a
# whole generated file would otherwise fill its context with it (S-9).
MAX_READ_LINES = 3000

# ---------------------------------------------------------------------------
# Tool 2 — the symbol's source
# ---------------------------------------------------------------------------

async def read_code_impl(
    *,
    graph_provider: IGraphDBProvider,
    connector_id: str,
    blob_store: BlobStorage,
    org_id: str,
    user_id: str,
    file_path: str,
    qualified_name: str | None = None,
    lines: str | None = None,
    max_lines: int | None = None,
    include_tests: bool = False,
    repo: str | None = None,
) -> dict[str, Any]:
    """Source for one symbol, one line range, or a whole file.

    All three modes cost the same single blob fetch -- ``read_record``
    returns every block of the file regardless -- so reading five symbols from
    one file should be one call, not five downloads of the same blob.

    ``max_lines`` bounds a whole-file read and applies by default, so asking for
    a file is safe without knowing its size. It is a budget, not a window: the
    caller says how much it can afford, and the file says where that lands. A
    line range is for when the caller already knows *which* part it wants.
    """
    # One payload for a missing file, a missing symbol and a denied record,
    # echoing only what the caller typed: a distinct message would say which.
    where = f"{file_path}#{qualified_name}" if qualified_name else file_path
    miss = {"error": f"No indexed code at {where!r}"}

    access = RecordAccess(graph_provider, org_id, user_id)
    if qualified_name:
        block = await resolve_symbol(
            graph_provider, org_id, file_path, qualified_name, connector_id,
            repo=repo, access=access,
        )
        if block is None:
            return miss
        record_id = block.get("recordId")
    else:
        block = None
        record_id = None

    loaded = await _load_blob(
        graph_provider=graph_provider, blob_store=blob_store, org_id=org_id,
        connector_id=connector_id, file_path=file_path, repo=repo,
        include_tests=include_tests, access=access, record_id=record_id, miss=miss,
    )
    if isinstance(loaded, dict):
        return loaded
    record_id, record_doc, record, file_path = loaded

    # `_record_id` is an internal handle the tool wrapper strips before the
    # model sees it: record-escalation keys on record ids, but every code tool
    # addresses files by path, and carrying both identities to the model invites
    # passing the wrong one.
    if qualified_name:
        # The blob is keyed by the stored spelling; `resolve_symbol` may have
        # matched the caller's name case-insensitively.
        found = _find_blob_block(record, block.get("qualifiedName") or qualified_name)
        if found is None:
            return {"error": f"No stored content for symbol {qualified_name!r}"}
        ref = SymbolRef.from_block(block)
        ref["connector_id"] = connector_id
        ref["code"] = found
        if link := line_link(record_doc.get("webUrl"), ref.get("start_line"), ref.get("end_line")):
            ref["web_url"] = link
        ref["_record_id"] = record_id
        return ref

    span = _parse_line_range(lines)
    if lines and span is None:
        return {"error": f"lines must look like '40-80', got {lines!r}"}
    budget = DEFAULT_MAX_LINES if max_lines is None else max(1, min(int(max_lines), MAX_READ_LINES))
    out = _file_code(record, file_path, connector_id, span, budget)
    names = await repo_names(graph_provider, org_id, [(record_doc or {}).get("recordGroupId")])
    if names:
        out["repo"] = next(iter(names.values()))
    if link := line_link(record_doc.get("webUrl"), *(span or (None, None))):
        out["web_url"] = link
    out["_record_id"] = record_id
    return out


def line_link(web_url: str | None, start: int | None, end: int | None) -> str | None:
    """The file's link with a line anchor, for citing what was read (A-12).

    The record's link is pinned to the commit it was synced at, so the lines
    stay the lines that were quoted. GitHub anchors `#L4-L9`, GitLab `#L4-9`.
    """
    if not web_url:
        return None
    if not start:
        return web_url
    if not end or end == start:
        return f"{web_url}#L{start}"
    return f"{web_url}#L{start}-{end}" if "/-/blob/" in web_url else f"{web_url}#L{start}-L{end}"


async def _load_blob(
    *,
    graph_provider: IGraphDBProvider,
    blob_store: BlobStorage,
    org_id: str,
    connector_id: str,
    file_path: str,
    repo: str | None,
    include_tests: bool,
    access: RecordAccess,
    record_id: str | None = None,
    miss: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any], dict[str, Any], str] | dict[str, Any]:
    """``(record_id, record node, stored record, indexed path)`` for one readable file, or the error.

    Every tool that reads a file's stored content goes through here, so a
    denied, missing or test file answers the same way from each of them.
    """
    miss = miss or {"error": f"No indexed code at {file_path!r}"}
    if record_id is None:
        record_ids, file_path = await _records_for_path(
            graph_provider, org_id, file_path,
            choose=lambda ids: _choose_records(
                graph_provider, org_id, ids, connector_id,
                file_path=file_path, repo=repo, access=access,
            ),
        )
        record_id = record_ids[0] if record_ids else None
        if record_id is None:
            return miss

    if record_id not in await access.readable([record_id]):
        return miss

    if not include_tests:
        roles = await get_record_roles(graph_provider, org_id, {record_id})
        if roles.get(record_id) == TEST_ROLE:
            return {"error": f"{file_path!r} is a test file; set include_tests=true to read it"}

    if blob_store is None:
        return {"error": "Blob storage is not available."}

    # The graph node deliberately carries no source: `data` is excluded from the
    # projection to keep traversed nodes small. The text lives in the blob.
    record_doc = await graph_provider.get_document(record_id, _RECORDS, raise_on_error=True)
    virtual_record_id = (record_doc or {}).get("virtualRecordId")
    if not virtual_record_id:
        return {"error": f"No stored content for {file_path!r}"}

    try:
        record = await record_artifact_reader(blob_store).read_record(virtual_record_id, org_id)
    except Exception as exc:
        logger.warning("Blob fetch failed for %s: %s", virtual_record_id, exc)
        return {"error": f"Failed to read stored content for {file_path!r}"}
    if not record:
        return {"error": f"No stored content for {file_path!r}"}
    return record_id, record_doc or {}, record, file_path


async def outline_impl(
    *,
    graph_provider: IGraphDBProvider,
    connector_id: str,
    blob_store: BlobStorage,
    org_id: str,
    user_id: str,
    file_path: str,
    include_tests: bool = False,
    repo: str | None = None,
) -> dict[str, Any]:
    """A file's definitions with their signatures, first doc line and line ranges (A-9).

    What a reader skims before reading the body: one blob fetch, no source.
    """
    access = RecordAccess(graph_provider, org_id, user_id)
    loaded = await _load_blob(
        graph_provider=graph_provider, blob_store=blob_store, org_id=org_id,
        connector_id=connector_id, file_path=file_path, repo=repo,
        include_tests=include_tests, access=access,
    )
    if isinstance(loaded, dict):
        return loaded
    _record_id, _record_doc, record, file_path = loaded
    symbols = []
    for meta in sorted(_named_spans(record), key=lambda m: (m["start_line"], m.get("end_line") or 0)):
        entry: dict[str, Any] = {
            "qualified_name": meta["qualified_name"],
            "lines": f"{meta['start_line']}-{meta.get('end_line') or meta['start_line']}",
        }
        if meta.get("signature"):
            entry["signature"] = meta["signature"]
        doc = (meta.get("docstring") or "").strip()
        if doc:
            entry["doc"] = doc.splitlines()[0][:200]
        if meta.get("decorators"):
            entry["decorators"] = meta["decorators"]
        symbols.append(entry)
    out: dict[str, Any] = {"file_path": file_path, "connector_id": connector_id, "symbols": symbols}
    error_line = _parse_error_line(record)
    if error_line:
        out["parse_error_line"] = error_line
        out["note"] = (f"The file has a syntax error at line {error_line}; definitions after it "
                       "may be missing. Read the source to check.")
    return out


def _parse_error_line(record: dict) -> int | None:
    blocks = ((record.get("block_containers") or {}).get("blocks")) or []
    for block in blocks:
        data = block.get("data") if isinstance(block, dict) else None
        if isinstance(data, dict) and data.get("kind") == "file_summary":
            line = data.get("parse_error_line")
            return line if isinstance(line, int) else None
    return None


def _parse_line_range(lines: str | None) -> tuple[int, int] | None:
    """``"40-80"`` -> ``(40, 80)``; ``None`` for absent, invalid stays ``None``."""
    if not lines:
        return None
    head, sep, tail = lines.partition("-")
    if not sep:
        head = tail = lines
    try:
        start, end = int(head.strip()), int(tail.strip())
    except ValueError:
        return None
    if start < 1 or end < start:
        return None
    return start, end


def _outer_spans(record: dict[str, Any]) -> list[dict[str, Any]]:
    """The spans of a file not contained in another, in source order.

    A group keeps its whole body and its members are subsets of it; a nested
    definition's bytes sit inside its parent block's. Only spans not contained
    in another are kept -- those tile the file, and concatenating them
    reconstructs it once. Spans with no line range (the file summary) are not
    source. When a group and one of its blocks cover the same lines (a class
    that is only its header), the group wins: it holds the body too.
    """
    containers = record.get("block_containers") or {}
    candidates: list[tuple[int, int, int, dict[str, Any]]] = []
    for rank, bucket in enumerate(("block_groups", "blocks")):
        for item in containers.get(bucket) or []:
            meta = item.get("code_metadata") or {}
            start = meta.get("start_line")
            if not start:
                continue
            end = meta.get("end_line") or start
            data = item.get("data")
            text = data.get("text") if isinstance(data, dict) else None
            if not text:
                continue
            candidates.append((start, -end, rank, {
                "qualified_name": meta.get("qualified_name"),
                "kind": meta.get("kind"),
                "start_line": start,
                "end_line": end,
                "code": text,
            }))
    candidates.sort(key=lambda item: item[:3])
    out: list[dict[str, Any]] = []
    covered_to = -1
    for _start, neg_end, _rank, entry in candidates:
        if -neg_end <= covered_to:
            continue
        covered_to = -neg_end
        out.append(entry)
    return out


def _named_spans(record: dict[str, Any]) -> list[dict[str, Any]]:
    containers = record.get("block_containers") or {}
    out = []
    for bucket in ("block_groups", "blocks"):
        for item in containers.get(bucket) or []:
            meta = item.get("code_metadata") or {}
            kind = (meta.get("kind") or "").lower()
            if meta.get("start_line") and meta.get("qualified_name") and kind not in FILLER_KINDS:
                out.append(meta)
    return out


def _file_code(
    record: dict[str, Any],
    file_path: str,
    connector_id: str,
    span: tuple[int, int] | None,
    budget: int,
) -> dict[str, Any]:
    """A whole file as its outermost spans, or exactly the lines of a range.

    A whole-file read keeps whole spans, so every fragment is a complete
    symbol, and stops at the budget. A line range is sliced to those lines:
    returning the span that contains them would answer "lines 900-910" with
    a 1,700-line class, and the budget clip would then cut the lines asked for.
    """
    spans = _outer_spans(record)
    if span:
        return _line_range(record, spans, file_path, connector_id, span, budget)
    kept: list[dict[str, Any]] = []
    spent = 0
    for entry in spans:
        length = max(1, entry["end_line"] - entry["start_line"] + 1)
        # Always keep the first block: a budget smaller than the opening symbol
        # should still return that symbol rather than nothing.
        if kept and spent + length > budget:
            break
        kept.append(entry)
        spent += length
    truncated = len(kept) < len(spans)
    out = {
        "file_path": file_path,
        "connector_id": connector_id,
        "lines": None,
        "truncated": truncated,
        "blocks": kept,
    }
    if truncated:
        resume = kept[-1]["end_line"] if kept else 0
        out["next"] = (
            f"Stopped at line {resume} of {spans[-1]['end_line']}. Continue "
            f"with lines='{(resume or 0) + 1}-{spans[-1]['end_line']}', or "
            "raise max_lines."
        )
    return out


def _line_range(
    record: dict[str, Any],
    spans: list[dict[str, Any]],
    file_path: str,
    connector_id: str,
    span: tuple[int, int],
    budget: int,
) -> dict[str, Any]:
    lines: dict[int, str] = {}
    for entry in spans:
        start, end = entry["start_line"], entry["end_line"]
        if end < span[0] or start > span[1]:
            continue
        for offset, text in enumerate(entry["code"].split("\n")):
            line = start + offset
            if line > end:
                break
            # A span can end mid-line (a header ends on the indentation of the
            # first member); the next span carries the rest of that line.
            lines[line] = lines.get(line, "") + text
    first, last = span[0], min(span[1], span[0] + budget - 1)
    present = [n for n in range(first, last + 1) if n in lines]
    out: dict[str, Any] = {
        "file_path": file_path,
        "connector_id": connector_id,
        "lines": f"{span[0]}-{span[1]}",
        "truncated": False,
        "blocks": [],
    }
    if not present:
        out["note"] = f"No source at lines {span[0]}-{span[1]}."
        return out
    first, last = present[0], present[-1]
    named = _named_spans(record)
    overlapping = sorted(
        (m for m in named if m["start_line"] <= last and (m.get("end_line") or 0) >= first),
        key=lambda m: (m["start_line"], -(m.get("end_line") or 0)),
    )
    enclosing = [m for m in overlapping
                 if m["start_line"] <= first and (m.get("end_line") or 0) >= last]
    innermost = min(enclosing, key=lambda m: m["end_line"] - m["start_line"], default=None)
    out["blocks"] = [{
        "qualified_name": innermost.get("qualified_name") if innermost else None,
        "kind": innermost.get("kind") if innermost else None,
        "start_line": first,
        "end_line": last,
        "code": "\n".join(lines[n] for n in range(first, last + 1) if n in lines),
    }]
    out["symbols"] = [m["qualified_name"] for m in overlapping[:30]]
    if last < span[1] and any(n > last for n in lines):
        out["truncated"] = True
        out["next"] = (
            f"Stopped at line {last} (max_lines={budget}). Continue with "
            f"lines='{last + 1}-{span[1]}', or raise max_lines."
        )
    return out


def _find_blob_block(record: dict[str, Any], qualified_name: str) -> str | None:
    """Match a graph block to its blob twin on ``code_metadata.qualified_name``.

    The graph `_key` is a hash and never appears in the blob, and the blob's own
    block ids are uuid4 -- the qualified name is the only shared identity. The blob is
    snake_case (no alias generators on the block models).
    """
    containers = record.get("block_containers") or {}
    for bucket in ("blocks", "block_groups"):
        for item in containers.get(bucket) or []:
            meta = item.get("code_metadata") or {}
            if meta.get("qualified_name") != qualified_name:
                continue
            data = item.get("data")
            if isinstance(data, dict):
                return data.get("text")
            if isinstance(data, str):
                return data
    return None
