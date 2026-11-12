"""Which processor write a sync uses for a record it has already read.

``on_new_records`` publishes ``newRecord``, and publishes nothing when the record
is already complete and unchanged. ``on_record_content_update`` publishes
``updateRecord`` unconditionally, which is what lets the indexer reconcile
blocks instead of embedding the record again. It is only for a real content
change on a record that already exists.
"""

from __future__ import annotations

from typing import Protocol

from app.models.entities import Record
from app.models.permission import Permission


class _RecordWriter(Protocol):
    async def on_new_records(
        self, records: list[tuple[Record, list[Permission]]],
    ) -> None: ...

    async def on_record_content_update(
        self, record: Record, permissions: list[Permission] | None = None,
    ) -> None: ...


# (record, grants, content changed at source)
SyncedItem = tuple[Record, list[Permission], bool]


def source_content_changed(existing: Record | None, incoming: Record) -> bool:
    """True when a stored, already-real record's source revision moved.

    A first write and a placeholder being filled in stay new records. An
    unchanged revision stays an upsert, which does not republish a completed
    record.
    """
    if existing is None or getattr(existing, "is_placeholder", False):
        return False
    return getattr(existing, "external_revision_id", None) != getattr(
        incoming, "external_revision_id", None
    )


def carry_stored_version(record: Record, existing: Record | None) -> bool:
    """Set ``record.version`` from the stored record when its content changed.

    Connectors that leave version at 0 let the processor fill it in, which is
    right for a first write. A content change has to arrive above 0, or it
    stays on ``newRecord``. Returns whether the source content changed.
    """
    if not source_content_changed(existing, record):
        return False
    stored = getattr(existing, "version", 0)
    record.version = (stored if isinstance(stored, int) else 0) + 1
    return True


def synced(record: Record, permissions: list[Permission], existing: Record | None) -> SyncedItem:
    """One row of a sync batch, with whether its source content changed."""
    return (record, permissions, source_content_changed(existing, record))


def _content_changed(item: tuple[object, ...]) -> bool:
    return bool(item[2]) if len(item) > 2 else False


async def save_synced_records(
    processor: _RecordWriter,
    records: list[tuple[Record, list[Permission]] | SyncedItem],
) -> None:
    """Write a sync batch on the path that matches what changed at source.

    New records, placeholders and unchanged records go to ``on_new_records``.
    A content change on a record that already has a version goes to
    ``on_record_content_update``. Version 0 is still a first write. Grants are
    passed through when the record rewrites them. Upserts keep their order and
    are written before content updates, so a new child of a page that is itself
    being updated still finds the page already stored.
    """
    upserts: list[tuple[Record, list[Permission]]] = []
    updates: list[tuple[Record, list[Permission]]] = []
    for item in records:
        record, permissions = item[0], item[1]
        if _content_changed(item) and getattr(record, "version", 0) > 0:
            updates.append((record, permissions))
        else:
            upserts.append((record, permissions))
    if upserts:
        await processor.on_new_records(upserts)
    for record, permissions in updates:
        if getattr(record, "rewrite_permissions", False):
            await processor.on_record_content_update(record, permissions)
        else:
            await processor.on_record_content_update(record)
