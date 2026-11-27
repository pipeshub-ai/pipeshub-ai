"""Record ids of Slack conversational bursts, shared by the Slack Workspace and Slack
Individual connectors, and the removal of the records a regroup leaves behind.

Messages a few minutes apart are stored as one burst record. Which messages share a
burst depends on what one read sees: a re-read with a newer message, another page
boundary or a thread read in full groups the same messages differently. A burst is
named after its first message, so one that only gained or lost later messages keeps its
id and its save replaces the stored copy. A record a regroup leaves behind is one an
earlier read named after a message that is now inside another record; after a save,
those names are looked up and the records found are deleted.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from app.models.entities import MessageRecord, RecordType

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from app.models.entities import Record


def channel_burst_id(channel_id: str, first_ts: str) -> str:
    return f"burst_{channel_id}_{first_ts}"


def thread_burst_id(channel_id: str, thread_ts: str, first_ts: str) -> str:
    return f"thread_burst_{channel_id}_{thread_ts}_{first_ts}"


def thread_group_id(channel_id: str, thread_ts: str) -> str:
    """The external group id of a thread's records."""
    return f"thread_{channel_id}_{thread_ts}"


def channel_record_ids(channel_id: str, ts: str) -> tuple[str, str]:
    """The ids a channel record starting at this message is stored under: a single, a burst."""
    return ts, channel_burst_id(channel_id, ts)


def thread_record_ids(channel_id: str, thread_ts: str, ts: str) -> tuple[str]:
    """The id a thread record starting at this message is stored under: every one is a burst."""
    return (thread_burst_id(channel_id, thread_ts, ts),)


def bursts_to_write(bursts: list[Any], *, replies_complete: bool | None) -> list[Any]:
    """The bursts a read of a thread's replies (all of them, or those after the sync point)
    can write. A read that stopped part way may have cut its last burst short; written, it
    would replace the stored burst of the same first reply, which an earlier read may have
    stored longer, and drop the replies not read. It waits for a read that gets them all."""
    return bursts if replies_complete else bursts[:-1]


def burst_revision(messages: Sequence[dict[str, Any]]) -> str:
    """Changes with the burst's messages, so a burst that keeps its id but gains or loses
    messages is indexed again."""
    all_ts = "|".join(sorted(m.get("ts", "") for m in messages))
    return hashlib.md5(all_ts.encode()).hexdigest()[:8]


def saved_records(
    records_with_permissions: Iterable[tuple[Record, Any]], saved: Iterable[Record] | None,
) -> list[Record]:
    """The records of a batch that ``on_new_records`` reports saved (its return value). One
    it quarantined or skipped is not among them, and neither is any when it reports nothing,
    so the messages of those keep the records they are stored in."""
    saved_ids = {id(r) for r in saved or ()}
    return [r for r, _ in records_with_permissions if id(r) in saved_ids]


def _message_ranges(records: Iterable[Record]) -> list[tuple[float, float]]:
    return [
        (float(r.start_ts), float(r.end_ts)) for r in records
        if isinstance(r, MessageRecord) and r.start_ts and r.end_ts
    ]


def unsaved_ts(msgs: Sequence[dict[str, Any]], saved: Iterable[Record]) -> set[str]:
    """The ts of the messages read that no saved message record spans."""
    ranges = _message_ranges(saved)
    return {
        ts for ts in (m.get("ts") for m in msgs)
        if ts and not any(start <= float(ts) <= end for start, end in ranges)
    }


def regrouped_record_ids(
    msgs: Sequence[dict[str, Any]],
    written: Iterable[Record],
    stored_ids: Callable[[str], Iterable[str]],
) -> list[str]:
    """Ids an earlier read may have stored these messages under, as the first message of a
    record, other than the records just written. Only messages inside a written message
    record count: one whose record was not built keeps what is stored."""
    written = list(written)
    written_ids = {r.external_record_id for r in written}
    ranges = _message_ranges(written)
    found: list[str] = []
    for msg in msgs:
        ts = msg.get("ts")
        if not ts or not any(start <= float(ts) <= end for start, end in ranges):
            continue
        found.extend(i for i in stored_ids(ts) if i not in written_ids and i not in found)
    return found


def _ts_ms(ts: str) -> int:
    return int(float(ts) * 1000)


class SlackRegroupMixin:
    """Deletes the records a regroup left behind. The host connector provides
    ``connector_id``, ``logger`` and ``data_entities_processor``."""

    async def _save_and_delete_regrouped(
        self,
        records: list[tuple[Record, Any]],
        msgs: Sequence[dict[str, Any]],
        stored_ids: Callable[[str], Iterable[str]],
        external_record_group_id: str,
        *,
        unsaved: set[str],
        delete: bool = True,
    ) -> None:
        """Save a batch built from ``msgs``, then (``delete``) delete the records a regroup
        left behind in this group. ``unsaved`` gathers, across the batches of one read, the
        messages no saved record holds; a stored record spanning one of them is kept."""
        saved = saved_records(records, await self.data_entities_processor.on_new_records(records)) if records else []
        unsaved |= unsaved_ts(msgs, saved)
        if delete:
            await self._delete_regrouped_records(
                regrouped_record_ids(msgs, saved, stored_ids), external_record_group_id, unsaved,
            )

    async def _delete_regrouped_records(
        self, external_ids: Iterable[str], external_record_group_id: str, unsaved: Iterable[str] = (),
    ) -> None:
        """Delete the stored message records under these ids in this group (a channel or a
        thread). Call it only after the records now holding their messages were saved, with
        ids found from the saved ones (``saved_records``). A stored record is deleted only
        when every message it may hold has a saved record: its messages are not stored, but
        it spans source_created_at to source_updated_at (first and last message, in ms), and
        one that spans an ``unsaved`` message, or does not say its span, is kept. A failed
        lookup or delete keeps the record. A single's id is its bare ts, which is unique
        only within a channel, so a record of another group is never deleted."""
        unsaved_ms = [_ts_ms(ts) for ts in unsaved]
        processor = self.data_entities_processor
        for external_id in external_ids:
            try:
                stored = await processor.get_record_by_external_id(
                    connector_id=self.connector_id, external_record_id=external_id,
                )
                if (
                    stored is None
                    or stored.record_type != RecordType.MESSAGE
                    or stored.external_record_group_id != external_record_group_id
                ):
                    continue
                first, last = stored.source_created_at, stored.source_updated_at
                if first is None or last is None or any(first <= ms <= last for ms in unsaved_ms):
                    self.logger.info(
                        f"Kept message record {external_id}: a message it may hold has no saved record"
                    )
                    continue
                await processor.on_record_deleted(stored.id)
                self.logger.info(
                    f"Deleted message record {external_id}: its messages are now in another record"
                )
            except Exception as exc:
                self.logger.warning(
                    f"Could not delete message record {external_id} left by a regroup; it is kept: {exc}"
                )
