"""Which MD5 duplicate decides a record's fate, and what that decision is.

Two records with identical content are duplicates of each other for *blob*
purposes always — the parsed content is stored once and reused. Whether they
are duplicates for *indexing* purposes depends on the collection strategy: a
record whose twin already has vectors in the collection this record would write
to needs no work, but a twin in a different collection has left nothing behind
here, so this record must still be indexed.

Under ``single`` every record resolves to one collection and this degenerates
to "any duplicate means skip". Under ``per_connector_type`` it means the skip
and queue decisions apply **within a connector type only** — a Slack copy of a
file cannot stand in for the Drive collection's missing vectors.

Kept apart from ``events.py`` because it is a pure policy over graph documents:
no I/O, no EventProcessor, and every branch of the matrix is worth testing
directly.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from app.config.constants.arangodb import ProgressStatus


@dataclass(frozen=True)
class DedupDecision:
    """Result of an MD5-duplicate check.

    ``virtual_record_id`` is set whenever a content duplicate was found,
    independent of whether indexing is skipped: content identity is
    collection-independent. ``skip_indexing`` is true only when this record's
    own collection already holds — or is about to hold — those vectors.
    """

    virtual_record_id: str | None = None
    skip_indexing: bool = False


@dataclass(frozen=True)
class DuplicateMatch:
    """The one duplicate that decides what happens to the record being processed."""

    record: Mapping[str, Any]
    same_collection: bool
    is_processed: bool
    """True for a duplicate that finished (COMPLETED/EMPTY), false for one still
    in flight. A finished twin can be reused; an in-flight one can only be
    waited on."""


def _is_processed(record: Mapping[str, Any]) -> bool:
    status = record.get("indexingStatus")
    if status == ProgressStatus.EMPTY.value:
        # An EMPTY record genuinely produced no vectors, so it is "done" —
        # reusing it means this record is empty too, not that it was indexed.
        return True
    return bool(record.get("virtualRecordId")) and status == ProgressStatus.COMPLETED.value


def _is_in_progress(record: Mapping[str, Any]) -> bool:
    return record.get("indexingStatus") == ProgressStatus.IN_PROGRESS.value


def select_duplicate(
    duplicates: Iterable[Mapping[str, Any]],
    current_collection: str | None,
    resolve_collection: Callable[[Mapping[str, Any]], str | None],
) -> DuplicateMatch | None:
    """Pick the duplicate whose state determines this record's handling.

    Ordered by how much work it saves, and that order is the whole point:

    1. **Same collection, finished** — nothing to do, the vectors are there.
    2. **Same collection, in flight** — wait for it rather than racing it into
       the same collection.
    3. **Other collection, finished** — lend the content identity, but this
       collection still needs its own vectors.
    4. **Other collection, in flight** — nothing to reuse and nothing worth
       waiting for; proceed.

    Scanning for the first duplicate of *any* status and only then asking which
    collection it belongs to — the obvious implementation — makes the answer
    depend on the order the graph happened to return rows in. A different
    collection's copy arriving first would send a record off to re-index
    content its own collection already holds, and worse, would let two records
    index the same content into one collection at the same time because the
    in-flight twin that should have queued it was never considered.

    ``resolve_collection`` may return None for a duplicate whose context cannot
    be resolved; such a record is treated as belonging elsewhere, so the
    conservative branch (index anyway) is taken rather than a skip that can
    never be repaired.
    """
    candidates = [d for d in duplicates if d]
    if not candidates:
        return None

    same, other = [], []
    for record in candidates:
        collection = resolve_collection(record)
        target = (
            same
            if current_collection is not None and collection == current_collection
            else other
        )
        target.append(record)

    for pool, same_collection in ((same, True), (other, False)):
        for predicate, is_processed in ((_is_processed, True), (_is_in_progress, False)):
            # Within a pool, finished beats in-flight; across pools, same
            # collection beats other. Hence pool first, status second.
            match = next((r for r in pool if predicate(r)), None)
            if match is not None:
                return DuplicateMatch(
                    record=match,
                    same_collection=same_collection,
                    is_processed=is_processed,
                )
    return None
