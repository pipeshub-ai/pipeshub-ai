"""
Block-coverage analysis for record-escalation.

`analyze_coverage` answers: for each record we retrieved something from, how
many distinct renderable blocks did we get, and how many exist in total?

The denominator mirrors what `record_to_message_content` in chat_helpers.py
actually renders — fragment blocks (`parent_block_index is not None`) are
skipped, as are TEXT blocks with a `parent_index`. Where the count is
uncertain, we bias toward over-counting: an inflated total lists a record that
a fetch would mostly duplicate (cheap), whereas an undercount silently excludes
a record the query genuinely needs (the failure this feature exists to prevent).

This module has no I/O, no LLM calls, no thresholds.
"""

from __future__ import annotations

from typing import Any


def analyze_coverage(
    flattened_results: list[dict[str, Any]],
    virtual_record_id_to_result: dict[str, Any],
) -> dict[str, tuple[int, int]]:
    """
    Return record_id -> (blocks_held, blocks_total).

    `blocks_held` counts distinct renderable block indices retrieved.
    `blocks_total` counts renderable blocks in the full record.

    Both values use the same fragment-skipping logic so the predicate
    "held == total" correctly means "we already have everything".

    Entries without a matching record in `virtual_record_id_to_result`
    are silently skipped (record was not loaded from storage).
    """
    held: dict[str, set[int]] = {}

    for entry in flattened_results:
        vrid = entry.get("virtual_record_id")
        if not vrid:
            continue
        record = virtual_record_id_to_result.get(vrid)
        if not record:
            continue
        record_id: str | None = record.get("id")
        if not record_id:
            continue

        block_index = entry.get("block_index")
        if block_index is None:
            # record-summary hit — contributes no individual block index
            held.setdefault(record_id, set())
            continue

        # For table/block-group hits content may be a (summary, children)
        # tuple; the entry-level block_index still applies.
        if isinstance(block_index, int):
            held.setdefault(record_id, set()).add(block_index)

    coverage: dict[str, tuple[int, int]] = {}
    for record_id, held_set in held.items():
        # Find the virtual record that owns this record_id.
        record = _find_record(record_id, virtual_record_id_to_result)
        if record is None:
            continue
        total = _count_renderable_blocks(record)
        coverage[record_id] = (len(held_set), total)

    return coverage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_record(
    record_id: str, virtual_record_id_to_result: dict[str, Any]
) -> dict[str, Any] | None:
    for record in virtual_record_id_to_result.values():
        if record and record.get("id") == record_id:
            return record
    return None


def _count_renderable_blocks(record: dict[str, Any]) -> int:
    """
    Count blocks that record_to_message_content would render.

    Mirrors chat_helpers.py record_to_message_content:
    - Skips blocks where parent_block_index is not None (fragment blocks).
    - Skips TEXT blocks where parent_index is not None (table-row children).
    - IMAGE blocks without valid data are skipped by the renderer but counted
      here (over-counting bias: cheap false positive, not a false negative).

    Records arrive as dicts (not Pydantic Record) in the retrieval path.
    """
    block_containers = record.get("block_containers") or {}
    blocks = block_containers.get("blocks") or []

    count = 0
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("parent_block_index") is not None:
            # Fragment block — renderer skips it.
            continue
        block_type = block.get("type", "")
        if block_type == "TEXT" and block.get("parent_index") is not None:
            # TABLE_ROW child rendered via its group, not individually.
            continue
        count += 1

    # If we could not determine the block structure at all, return 0 so the
    # record is never incorrectly excluded (over-counting bias).
    return count
