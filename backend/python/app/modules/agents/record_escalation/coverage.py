"""
Block-coverage analysis for record-escalation.

`analyze_coverage` answers: for each record we retrieved something from, how
many distinct blocks did we get, and how many exist in total?

Numerator and denominator must range over the same set of blocks, or the
"held == total" predicate stops meaning "we already have everything".
Retrieval addresses group children directly — a table or list hit is
flattened with `block_index` pointing at a child row (see
`get_flattened_results` in chat_helpers.py) — so children must be counted on
both sides. Only fragment blocks (`parent_block_index is not None`, produced
by splitting a container around an inline image) are excluded from both,
because retrieval routes those through their container's index, never their
own.

Where the count is uncertain we bias toward over-counting the total: an
inflated total lists a record that a fetch would mostly duplicate (cheap),
whereas an undercount silently excludes a record the query genuinely needs
(the failure this feature exists to prevent).

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

    `blocks_held` counts distinct countable block indices retrieved.
    `blocks_total` counts countable blocks in the full record.

    Both values range over the same set of blocks (see module docstring), so
    the predicate "held == total" correctly means "we already have
    everything" — and `held` can never exceed `total`, which would otherwise
    make a barely-covered record look fully retrieved.

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
        if isinstance(block_index, int) and _is_countable_index(record, block_index):
            held.setdefault(record_id, set()).add(block_index)

    coverage: dict[str, tuple[int, int]] = {}
    for record_id, held_set in held.items():
        # Find the virtual record that owns this record_id.
        record = _find_record(record_id, virtual_record_id_to_result)
        if record is None:
            continue
        total = _count_blocks(record)
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


def _is_fragment(block: dict[str, Any]) -> bool:
    """A block split off a container around an inline image. Retrieval never
    reports a fragment's own index — it substitutes the container's — so
    fragments are excluded from both sides of the ratio."""
    return block.get("parent_block_index") is not None


def _is_countable_index(record: dict[str, Any], block_index: int) -> bool:
    """Whether `block_index` refers to a block `_count_blocks` also counts.

    Guards the numerator against indices the denominator does not include:
    counting one without the other let `held` exceed `total` on
    table/list-heavy records, which `build_candidates` then read as "all
    blocks already retrieved" and dropped from the candidate list.

    An index we cannot resolve (out of range, unknown structure) counts —
    over-counting `held` only understates the gap for one block, whereas
    dropping it silently understates how much the model already holds.
    """
    block_containers = record.get("block_containers") or {}
    blocks = block_containers.get("blocks") or []
    if not (0 <= block_index < len(blocks)):
        return True
    block = blocks[block_index]
    if not isinstance(block, dict):
        return True
    return not _is_fragment(block)


def _count_blocks(record: dict[str, Any]) -> int:
    """
    Count the blocks a full fetch of this record would surface.

    Group children (table rows, list items) ARE counted: retrieval addresses
    them by their own `block_index`, so leaving them out of the total while
    the numerator counts them made `held > total` possible.

    IMAGE blocks without valid data are skipped by the renderer but counted
    here (over-counting bias: cheap false positive, not a false negative).

    Records arrive as dicts (not Pydantic Record) in the retrieval path.
    """
    block_containers = record.get("block_containers") or {}
    blocks = block_containers.get("blocks") or []

    return sum(
        1 for block in blocks
        if isinstance(block, dict) and not _is_fragment(block)
    )
