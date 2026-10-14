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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

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

class _LabelMapper(CitationRefMapper):
    """Mints citation refs no document can contain, so the labels the renderer
    writes with them (`[ref3] `, `[12|ref3] `) can be removed exactly.

    A ref is NUL-delimited (`\\x00 3 \\x00`) and never longer than the
    `ref3` it stands for, so a render priced with it stays within budget once
    the labels are gone. An API caller has no use for the labels: they only
    serve the assistant's citations.
    """

    def get_or_create_ref(self, full_url: str) -> str:
        if full_url in self._url_to_ref:
            return self._url_to_ref[full_url]
        self._counter += 1
        ref = f"\x00{self._counter}\x00"
        self._url_to_ref[full_url] = ref
        self._ref_to_url[ref] = full_url
        return ref

    def peek_ref(self, full_url: str) -> str:
        return self._url_to_ref.get(full_url) or f"\x00{self._counter + 1}\x00"


# `[<ref>] ` before a block, `[<block index>|<ref>]` before a table row or a
# group child: with `_LabelMapper` refs, only the renderer writes these.
_BLOCK_LABEL = re.compile("\\[\x00\\d+\x00\\] ?")
_ROW_LABEL = re.compile("\\[([^\\[\\]|\\n]*)\\|\x00\\d+\x00\\]")
# A row the renderer adds itself carries no ref, and always reads "[…": a
# skipped range, or "the rest did not fit".
_OWN_ROW_LABEL = re.compile("^\\[(\\d+|…)\\|\\] (?=\\[…)", re.MULTILINE)


def _strip_labels(text: str) -> str:
    """The rendered text without the citation labels the renderer wrote.

    Document text is never touched: a line reading "[ref1] is cited",
    "[note|] keep" or "[0|ref1] invoice" is the document's own.
    """
    text = _BLOCK_LABEL.sub("", text)
    text = _ROW_LABEL.sub(lambda m: f"[{m.group(1)}]", text)
    return _OWN_ROW_LABEL.sub(lambda m: f"[{m.group(1)}] ", text)


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
    clipped_note: Callable[[RecordRenderOutcome], str | None] | None = None,
) -> str:
    """The record's text as the caller reads it.

    `clipped_note`, given the render's outcome, may return a note to stand in
    for the renderer's own trailing "read on from block N" hint -- the hint
    names the block after one that was cut, and a response must not carry two
    different places to continue from.
    """
    mapper = _LabelMapper()
    content, mapper = record_to_message_content(
        record,
        ref_mapper=mapper,
        start_block=start_block,
        budget=budget,
        include_blocks=include_blocks,
        hints=API_READ_HINTS,
    )
    texts = [_strip_labels(item.get("text", "")) for item in content if item.get("type") == "text"]
    note = clipped_note(budget.outcome(_record_key(record))) if clipped_note else None
    if note is not None:
        # The trailing hint is the last "[Record …" piece; the record's
        # foreign-key lines and closing tag come after it.
        hint = next(
            (i for i in range(len(texts) - 1, -1, -1) if texts[i].startswith("\n[Record ")), None,
        )
        if hint is None:
            texts.insert(len(texts) - 1 if texts else 0, note)
        else:
            texts[hint] = note
    return "".join(texts)


def _clip(
    record: dict[str, Any],
    offset: int,
    max_chars: int,
    candidates: set[int] | None = None,
) -> tuple[Callable[[RecordRenderOutcome], str | None], dict[str, Any]]:
    """What to do about a block cut because it alone was larger than the budget.

    The renderer reports the block *after* a cut one as where to resume, so
    following it would lose the rest of the cut block. Below the largest
    `maxChars` the cursor stays on the cut block and the note says to send the
    same cursor with more room. At the largest, nothing can return the block
    whole, so the cursor moves on (no loop) and the note says so beside the
    renderer's own hint.

    Returns the `_render` callback and a dict it fills with `block` (the cut
    block) and `hold` (whether the cursor stays on it).
    """
    found: dict[str, Any] = {"block": None, "hold": False}

    def _note(outcome: RecordRenderOutcome) -> str | None:
        block = _clipped_block(record, outcome, offset, candidates)
        if block is None:
            return None
        found["block"] = block
        found["hold"] = max_chars < MAX_READ_CHARS
        return _oversized_note(record.get("id"), block, max_chars) if found["hold"] else None

    return _note, found


def _truncated_by(outcome: RecordRenderOutcome) -> str | None:
    if outcome.complete:
        return None
    # A cut block is the loss a reader most needs to know about; the stop
    # that followed it is only where the walk ended.
    if outcome.clipped:
        return "clipped"
    return outcome.stop_reason or "chars"


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
    # Room for the note that may stand in for the trailing hint.
    budget.charge_framing_chars(_OVERSIZED_NOTE_RESERVE + len(str(record.get("id") or "")))

    clipped_note, clip = _clip(record, start, max_chars, include_blocks)
    text = _render(
        record, budget, start_block=start, include_blocks=include_blocks, clipped_note=clipped_note,
    )
    outcome = budget.outcome(key)
    indices = _renderable_block_indices(record)
    next_start = None if outcome.complete else outcome.stopped_at_block
    complete = outcome.complete
    given_up = clip["block"] if clip["block"] is not None and not clip["hold"] else None
    if clip["block"] is not None:
        if clip["hold"]:
            next_start = clip["block"]
        else:
            text += _oversized_note(record.get("id"), clip["block"], max_chars)
            # At the largest `maxChars` the cut block is given up. With nothing
            # after it there is nothing left to read: `complete` says so, so a
            # caller reading until complete stops; `truncatedBy` stays
            # `clipped` and the note says what was lost.
            complete = next_start is None

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
        "complete": complete,
        "truncatedBy": _truncated_by(outcome),
        # A block cut at the largest `maxChars` whose rest no read can return.
        # Named here, in the read that gave it up, whatever else the read did.
        "givenUpBlock": given_up,
        "nextStartBlock": next_start,
        "chars": len(text),
        "maxChars": max_chars,
    }


def _thread_note(
    shown_from: int, shown_to: int, total: int, next_record: int, next_block: int,
) -> str:
    return (
        f"\n[Thread: showed records {shown_from + 1}–{shown_to + 1} of {total}. "
        f"To read on, request this thread again with startRecord={next_record} "
        f"and startBlock={next_block}.]\n"
    )


# Longest `_oversized_note` without its record id, for reserving room.
_OVERSIZED_NOTE_RESERVE = 240


def _oversized_note(record_id: object, block: int, max_chars: int) -> str:
    if max_chars >= MAX_READ_CHARS:
        return (
            f"\n[Block {block} of record {record_id} is larger than the largest "
            f"maxChars ({MAX_READ_CHARS}) and was cut; the rest of that block "
            f"cannot be returned.]\n"
        )
    return (
        f"\n[Block {block} of record {record_id} is larger than maxChars={max_chars} "
        f"and was cut. To read it whole, request it again from startBlock={block} "
        f"with a larger maxChars (up to {MAX_READ_CHARS}).]\n"
    )


def _clipped_block(
    record: dict[str, Any],
    outcome: RecordRenderOutcome,
    offset: int,
    candidates: set[int] | None = None,
) -> int | None:
    """The block a read cut short because it alone was larger than the budget.

    Only the first block a read shows for a record can be clipped, and a
    clipped block is never counted as shown, so it is the first block from
    `offset` that the read did not show.
    """
    if not outcome.clipped:
        return None
    return next(
        (
            i for i in _renderable_block_indices(record)
            if i >= offset and i not in outcome.shown_blocks
            and (candidates is None or i in candidates)
        ),
        None,
    )


def _widest_block(records: list[dict[str, Any]]) -> int:
    """The largest block offset a thread cursor can name, for reserving room."""
    return max((max(_renderable_block_indices(r), default=0) for r in records), default=0) + 1


async def read_thread(
    records: list[dict[str, Any]],
    *,
    max_chars: int = DEFAULT_READ_CHARS,
    start_record: int = 0,
    start_block: int = 0,
) -> dict[str, Any]:
    """The records of one Slack thread, oldest first, within `max_chars`.

    The cursor is a record and a block in it: `start_block` applies to the
    record at `start_record`, and every later record is read from its start.
    Records are read whole and in order, because a conversation read with a
    reply missing in the middle reads as a different conversation. A record
    that does not fit is left for the next read -- unless it is the first one
    of this read, which is read as far as it fits so that every read makes
    progress. The cursor then stays on that record, at the block where it
    stopped, and moves on only once the record has been read to its end.
    """
    total = len(records)
    budget = RenderBudget(max_chars=max_chars, max_blocks=FETCH_SAFETY_MAX_BLOCKS)
    widest_record = 10 ** len(str(total + 1)) - 1
    widest_block = 10 ** len(str(_widest_block(records))) - 1
    # Held back so the closing note always fits.
    widest_id = max((len(str(r.get("id") or "")) for r in records), default=0)
    budget.charge_framing_chars(
        len(_thread_note(widest_record, widest_record, total, widest_record, widest_block))
        + len(_oversized_note("x" * widest_id, widest_block, 1))
    )

    parts: list[str] = []
    shown: list[dict[str, Any]] = []
    next_record: int | None = None
    next_block = 0
    oversized = ""
    for position in range(start_record, total):
        record = records[position]
        key = _record_key(record)
        offset = start_block if position == start_record else 0
        budget.begin_record(key)
        if shown and estimate_render_chars(record) > budget.call_chars_remaining:
            next_record, next_block = position, 0
            break
        budget.allot(key, None)
        clipped_note, clip = _clip(record, offset, max_chars)
        parts.append(_render(
            record, budget, start_block=offset, include_blocks=None, clipped_note=clipped_note,
        ))
        outcome = budget.outcome(key)
        resume = None if outcome.complete else outcome.stopped_at_block
        clipped = clip["block"]
        if clipped is not None:
            # A block larger than the whole budget was cut; see `_clip`. When
            # the cursor holds, the note already stands in for the record's
            # own hint; when it cannot, the note goes beside it.
            if clip["hold"]:
                resume = clipped
            else:
                oversized = _oversized_note(record.get("id"), clipped, max_chars)
        shown.append({
            "recordId": record.get("id"),
            "recordName": record.get("record_name"),
            "startBlock": offset,
            "complete": outcome.complete,
            "truncatedBy": _truncated_by(outcome),
            "givenUpBlock": clipped if clipped is not None and not clip["hold"] else None,
            "nextStartBlock": resume,
        })
        if not outcome.complete:
            # Nothing after a record that was cut short: the cursor stays on
            # it, at the block where it stopped, until it is read to its end.
            if resume is not None and (clipped is not None or position != start_record or resume > offset):
                next_record, next_block = position, resume
            elif position + 1 < total:
                next_record, next_block = position + 1, 0
            break

    text = "\n".join(parts) + oversized
    if next_record is not None and shown:
        text += _thread_note(
            start_record, start_record + len(shown) - 1, total, next_record, next_block,
        )
    elif start_record >= total:
        text = f"[No records at startRecord={start_record}: this thread has {total}.]\n"

    return {
        "content": text,
        "mode": "thread",
        "threadRecordCount": total,
        "startRecord": start_record,
        "startBlock": start_block,
        "records": shown,
        # Nothing of the thread is left to read. A record whose only loss is
        # a block given up at the largest `maxChars` (its `truncatedBy` is
        # `clipped`, with no `nextStartBlock`) has nothing left either.
        "complete": next_record is None and all(
            r["complete"] or (r["givenUpBlock"] is not None and r["nextStartBlock"] is None)
            for r in shown
        ),
        # Every block this read gave up at the largest `maxChars`.
        "givenUpBlocks": [
            {"recordId": r["recordId"], "block": r["givenUpBlock"]}
            for r in shown if r["givenUpBlock"] is not None
        ],
        "nextStartRecord": next_record,
        "nextStartBlock": next_block if next_record is not None else None,
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
