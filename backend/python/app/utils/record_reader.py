"""Part of a record, or a whole Slack thread, read within a character budget.

The record-content API used to return a record whole. A pull request with its
diffs or a long document comes to hundreds of thousands of characters, which no
caller's tool-output limit holds, and nothing in the response said how to get
the rest. The assistant's own fetch tool solved this already: a character
budget (`RenderBudget`), a renderer that stops at it and says where it stopped
(`record_to_message_content`), and, for a record that does not fit, the blocks
most relevant to the question first (`select_relevant_blocks`).

This module is only the glue that lets an API caller use that reader. It adds
no rendering or ranking of its own:

* `read_record` reads one record from `start_block`, or the blocks relevant to
  `query` when the record does not fit, and reports the offset that continues
  it.
* `read_thread` reads the records of one Slack thread in order, as returned by
  `fetch_slack_thread`'s permission-checked listing, and reports the record
  that continues it.

Hints in the text use the API's own parameters (`API_READ_HINTS`), not the
assistant's tool name, and the citation labels the assistant needs (`[ref3]`)
are taken out: an API caller has no use for them.
"""

from __future__ import annotations

import re
from typing import Any

from app.utils.chat_helpers import (
    CitationRefMapper,
    _renderable_block_indices,
    record_to_message_content,
)
from app.utils.record_block_selection import (
    API_READ_HINTS,
    estimate_frame_chars,
    estimate_render_chars,
    select_relevant_blocks,
)
from app.utils.render_budget import (
    FETCH_MAX_CHARS,
    FETCH_SAFETY_MAX_BLOCKS,
    RecordRenderOutcome,
    RenderBudget,
)

# What a caller gets when it names no budget: about 10k tokens, well inside
# the tool-output limit of common agent clients.
DEFAULT_READ_CHARS = 40_000
# Smaller than this holds a record's header and little else.
MIN_READ_CHARS = 2_000
# The assistant's own ceiling for one fetch.
MAX_READ_CHARS = FETCH_MAX_CHARS

# A citation label at the start of a rendered line: `[ref3] ` before a block,
# `[12|ref3] ` (or `[12|] `) before a table row or a group's child.
_LEADING_REF = re.compile(r"^\[(?:ref\d+)\] ", re.MULTILINE)
_ROW_REF = re.compile(r"^\[([^\[\]|\n]*)\|(?:ref\d+)?\]", re.MULTILINE)


def _strip_refs(text: str, refs: set[str]) -> str:
    """The rendered text without the citation labels this render minted.

    Only labels the mapper actually issued are touched, so a document line
    that happens to begin with "[ref1]" keeps it. Removing text never pushes
    a read past its budget.
    """
    def _leading(match: re.Match[str]) -> str:
        return "" if match.group(0)[1:-2] in refs else match.group(0)

    def _row(match: re.Match[str]) -> str:
        ref = match.group(0).rsplit("|", 1)[1][:-1]
        return f"[{match.group(1)}]" if ref == "" or ref in refs else match.group(0)

    return _ROW_REF.sub(_row, _LEADING_REF.sub(_leading, text))


def _ranges(indices: frozenset[int] | set[int]) -> list[list[int]]:
    """`{0, 1, 2, 7, 8}` as `[[0, 2], [7, 8]]`: which blocks were read."""
    ranges: list[list[int]] = []
    for index in sorted(indices):
        if ranges and index == ranges[-1][1] + 1:
            ranges[-1][1] = index
        else:
            ranges.append([index, index])
    return ranges


def _record_key(record: dict[str, Any]) -> str:
    return str(record.get("id") or record.get("virtual_record_id") or "")


def _render(
    record: dict[str, Any],
    budget: RenderBudget,
    *,
    start_block: int,
    include_blocks: set[int] | None,
) -> str:
    mapper = CitationRefMapper()
    content, mapper = record_to_message_content(
        record,
        ref_mapper=mapper,
        start_block=start_block,
        budget=budget,
        include_blocks=include_blocks,
        hints=API_READ_HINTS,
    )
    text = "".join(item.get("text", "") for item in content if item.get("type") == "text")
    return _strip_refs(text, set(mapper.ref_to_url))


def _truncated_by(outcome: RecordRenderOutcome) -> str | None:
    if outcome.complete:
        return None
    if outcome.stop_reason:
        return outcome.stop_reason
    return "clipped" if outcome.clipped else "chars"


async def read_record(
    record: dict[str, Any],
    *,
    max_chars: int = DEFAULT_READ_CHARS,
    start_block: int | None = None,
    query: str | None = None,
    retrieval_service: Any = None,  # noqa: ANN401
    user_id: str | None = None,
    org_id: str | None = None,
) -> dict[str, Any]:
    """One record, rendered within `max_chars`.

    Read in order from `start_block` (0 when not given). When `query` is given
    and `start_block` is not, a record that does not fit shows the blocks
    most relevant to the query instead of its first pages, with the gaps
    marked. An explicit `start_block`, 0 included, always reads in order: it
    is what the hints tell a caller to send to read on, or to read a selected
    record from the top.
    """
    budget = RenderBudget(max_chars=max_chars, max_blocks=FETCH_SAFETY_MAX_BLOCKS)
    key = _record_key(record)
    budget.begin_record(key)
    start = start_block or 0

    include_blocks: set[int] | None = None
    fits = estimate_render_chars(record, start) <= max_chars
    if query and query.strip() and start_block is None and not fits:
        include_blocks = await select_relevant_blocks(
            record=record,
            virtual_record_id=record.get("virtual_record_id"),
            query=query.strip(),
            retrieval_service=retrieval_service,
            user_id=user_id or "",
            org_id=org_id or "",
            budget=budget,
            reserve=estimate_frame_chars(record, selection=True),
        )
    budget.allot(key, None, unbounded_blocks=include_blocks is not None)

    text = _render(record, budget, start_block=start, include_blocks=include_blocks)
    outcome = budget.outcome(key)
    indices = _renderable_block_indices(record)

    if start > 0 and outcome.blocks_rendered == 0 and not any(i >= start for i in indices):
        last = max(indices) if indices else -1
        # Fits: the record rendered nothing but its frame.
        text += (
            f"\n[No blocks at startBlock={start}: this record's last block is {last}.]\n"
        )

    return {
        "content": text,
        "mode": "record",
        "recordId": record.get("id"),
        "order": "relevance" if include_blocks is not None else "in_order",
        "startBlock": start,
        "totalBlocks": len(indices),
        "shownBlocks": _ranges(outcome.shown_blocks),
        "complete": outcome.complete,
        "truncatedBy": _truncated_by(outcome),
        "nextStartBlock": None if outcome.complete else outcome.stopped_at_block,
        "chars": len(text),
        "maxChars": max_chars,
    }


def _thread_note(shown_from: int, shown_to: int, total: int, next_record: int) -> str:
    return (
        f"\n[Thread: showed records {shown_from + 1}–{shown_to + 1} of {total}. "
        f"To read on, request this thread again with startRecord={next_record}.]\n"
    )


async def read_thread(
    records: list[dict[str, Any]],
    *,
    max_chars: int = DEFAULT_READ_CHARS,
    start_record: int = 0,
) -> dict[str, Any]:
    """The records of one Slack thread, oldest first, within `max_chars`.

    Records are read whole and in order, because a conversation read with a
    reply missing in the middle reads as a different conversation. A record
    that does not fit is left for the next read -- unless it is the first one
    of this read, which is read as far as it fits (with its own `startBlock`
    to continue it) so that every read makes progress.
    """
    total = len(records)
    budget = RenderBudget(max_chars=max_chars, max_blocks=FETCH_SAFETY_MAX_BLOCKS)
    widest = len(str(total + 1))
    # Held back so the closing note always fits.
    budget.charge_framing_chars(len(_thread_note(10**widest - 1, 10**widest - 1, total, 10**widest - 1)))

    parts: list[str] = []
    shown: list[dict[str, Any]] = []
    next_record: int | None = None
    for position in range(start_record, total):
        record = records[position]
        key = _record_key(record)
        budget.begin_record(key)
        if shown and estimate_render_chars(record) > budget.call_chars_remaining:
            next_record = position
            break
        budget.allot(key, None)
        parts.append(_render(record, budget, start_block=0, include_blocks=None))
        outcome = budget.outcome(key)
        shown.append({
            "recordId": record.get("id"),
            "recordName": record.get("record_name"),
            "complete": outcome.complete,
            "nextStartBlock": None if outcome.complete else outcome.stopped_at_block,
        })
        if not outcome.complete:
            # Nothing after a record that was cut short: the reader continues
            # it first, from the offset its hint gave.
            next_record = position + 1 if position + 1 < total else None
            break

    text = "\n".join(parts)
    if next_record is not None and shown:
        text += _thread_note(start_record, start_record + len(shown) - 1, total, next_record)
    elif start_record >= total:
        text = f"[No records at startRecord={start_record}: this thread has {total}.]\n"

    return {
        "content": text,
        "mode": "thread",
        "threadRecordCount": total,
        "startRecord": start_record,
        "records": shown,
        # Nothing of the thread is left after this read.
        "complete": next_record is None and all(r["complete"] for r in shown),
        "nextStartRecord": next_record,
        "chars": len(text),
        "maxChars": max_chars,
    }


__all__ = [
    "DEFAULT_READ_CHARS",
    "MAX_READ_CHARS",
    "MIN_READ_CHARS",
    "read_record",
    "read_thread",
]
