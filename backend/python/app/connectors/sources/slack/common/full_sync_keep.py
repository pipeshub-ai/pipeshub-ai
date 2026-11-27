"""Full-sync keep logic shared by the Slack Workspace and Slack Individual connectors.

A full sync unlinks every connector node it did not write or note kept. What Slack
refuses to show is not gone: a channel whose history is refused keeps its stored
records, a thread whose replies are refused keeps its stored replies and is read again
on later syncs (a per-channel pending list in the KV store), and a channel checkpoint
waits until everything before it was read.

The connectors mix this in; each keeps its own differences through the class
attributes and the hook below.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from app.config.configuration_service import delete_config_confirmed
from app.connectors.core.base.sync_point.sync_point import (
    generate_record_sync_point_key,
)
from app.connectors.sources.slack.common.rate_limits import Tier
from app.models.entities import Record, RecordType
from app.services.graph_db.common.record_visibility import RecordVisibility

if TYPE_CHECKING:
    from collections.abc import Sequence

# Threads per channel whose replies Slack refused and that later syncs read again,
# whatever their age; past this the oldest refusal is dropped, until a full sync.
MAX_PENDING_THREADS_PER_CHANNEL = 20


class SlackFullSyncKeepMixin:
    """Keep what Slack refuses to show through a full sync, and read it again later.

    The host connector provides ``connector_id``, ``logger``, ``config_service``,
    ``data_entities_processor``, ``rate_limiter``, ``messages_sync_point``,
    ``audit_log_sync_point``, ``_fresh_datasource``, ``_process_thread``,
    ``_process_thread_incremental``, ``_handle_new_thread`` and the per-run
    ``_kept_channel_ids``, ``_kept_channel_read_from`` and ``_full_sync_read_failed``.
    """

    # conversations.replies page size.
    _replies_page_size: int = 200
    # An emptied pending list deletes its KV key instead of storing an empty list.
    _delete_empty_pending_threads: bool = False

    async def _finish_channel_messages(
        self,
        channel_id: str,
        sync_key: str,
        deferred_threads: Sequence[Any],
        *,
        newest_ts: Optional[str],
        oldest_read: Optional[str],
        history_error: Optional[str],
        total_msgs: int,
    ) -> None:
        """Process the channel's deferred threads, retry its pending ones, and move its
        checkpoint only when everything before it was read.

        ``deferred_threads`` hold ``msg``, ``ctx`` and ``rg_id``. ``history_error`` is
        set when a history page failed for a reason other than a refusal.
        """
        # Threads whose replies were not all read (a failed replies page, or
        # an exception while processing the thread). Their parents must stay
        # inside the next sync's window, so the channel checkpoint waits too.
        incomplete_threads = 0

        # Process deferred threads after all channel records have been published.
        # Full vs. incremental is gated on the per-thread sync_point, not on RG-node
        # existence: a full sync deletes sync_points and sync edges but keeps RG
        # nodes, so gating on RG existence would silently take the incremental
        # path on full sync and leave the thread RG's BELONGS_TO /
        # INHERIT_PERMISSIONS / PERMISSION edges unrecreated.
        # Sync_point absent ⇒ _process_thread (re-emits the RG and refetches all
        # replies, recreating every edge through on_new_record_groups /
        # on_new_records).
        # Threads whose replies Slack refused on an earlier sync; read again below.
        pending_threads = await self._read_pending_threads(channel_id)
        pending_before = list(pending_threads)
        handled_ts: set[str] = set()
        if deferred_threads:
            self.logger.info(
                f"Processing {len(deferred_threads)} deferred threads for channel {channel_id}"
            )
            for dt in deferred_threads:
                ts = dt.msg.get("ts", "")
                handled_ts.add(ts)
                thread_sp_key = generate_record_sync_point_key(
                    RecordType.MESSAGE.value, "slack_thread",
                    f"{dt.ctx.channel_id}_{ts}",
                )
                try:
                    sp_data = await self.audit_log_sync_point.read_sync_point(
                        thread_sp_key
                    )
                    last_reply_ts = (sp_data or {}).get("last_reply_ts")

                    existing_rg_id = ""
                    if last_reply_ts:
                        try:
                            existing_rg = await self.data_entities_processor.get_record_group_by_external_id(
                                connector_id=self.connector_id,
                                external_id=f"thread_{dt.ctx.channel_id}_{ts}",
                            )
                            if existing_rg:
                                existing_rg_id = existing_rg.id or ""
                        except Exception as exc:
                            self.logger.debug(
                                f"[DeferredThread] RG lookup for thread {ts}: {exc}"
                            )

                    # No checkpoint, or checkpoint exists but the RG node was
                    # removed (orphan sync_point) ⇒ full path so the RG is
                    # rebuilt and edges reasserted.
                    if not last_reply_ts or not existing_rg_id:
                        self.logger.info(
                            f"[DeferredThread] Processing thread parent ts={ts}, "
                            f"reply_count={dt.msg.get('reply_count')}, channel={dt.ctx.channel_id} (full)"
                        )
                        replies_complete = await self._process_thread(
                            dt.msg, dt.ctx, dt.rg_id
                        )
                    else:
                        self.logger.info(
                            f"[DeferredThread] Processing thread parent ts={ts}, "
                            f"reply_count={dt.msg.get('reply_count')}, channel={dt.ctx.channel_id} (incremental)"
                        )
                        replies_complete = await self._process_thread_incremental(
                            dt.msg, dt.ctx, existing_rg_id, last_reply_ts
                        )
                    if replies_complete is None:
                        # Kept, not read: later syncs read it again, however old.
                        if ts not in pending_threads:
                            pending_threads.append(ts)
                        continue

                    if not replies_complete:
                        incomplete_threads += 1
                        self.logger.warning(
                            f"[DeferredThread] Thread checkpoint NOT updated for ts={ts}, "
                            f"channel={dt.ctx.channel_id}: replies were only partly fetched"
                        )
                        continue

                    latest_reply = dt.msg.get("latest_reply") or ts
                    await self.audit_log_sync_point.update_sync_point(
                        thread_sp_key, {"last_reply_ts": latest_reply}
                    )
                    if ts in pending_threads:
                        pending_threads.remove(ts)
                except Exception as exc:
                    incomplete_threads += 1
                    self.logger.error(
                        f"[DeferredThread] Thread processing FAILED for ts={ts}, "
                        f"channel={dt.ctx.channel_id}: {exc}",
                        exc_info=True,
                    )

        if not history_error and channel_id not in self._kept_channel_ids:
            for ts in [t for t in pending_threads if t not in handled_ts]:
                if await self._retry_pending_thread(channel_id, ts):
                    pending_threads.remove(ts)
        if pending_threads != pending_before:
            await self._write_pending_threads(channel_id, pending_threads)

        if history_error or incomplete_threads:
            self._full_sync_read_failed = True
        if oldest_read and channel_id in self._kept_channel_ids:
            self._kept_channel_read_from[channel_id] = oldest_read

        # Persist checkpoint ONLY after all pages succeed
        if history_error:
            self.logger.warning(
                f"Checkpoint NOT updated for {channel_id}: conversations.history "
                f"failed part-way ({history_error}) after {total_msgs} messages; "
                f"the next sync starts again from the previous checkpoint"
            )
        elif incomplete_threads:
            self.logger.warning(
                f"Checkpoint NOT updated for {channel_id}: replies of "
                f"{incomplete_threads} thread(s) could not all be read; the next "
                f"sync starts again from the previous checkpoint"
            )
        elif newest_ts and channel_id in self._kept_channel_ids:
            # Slack refused a later page, so the older messages were never read.
            self.logger.warning(
                f"Checkpoint NOT updated for {channel_id}: Slack stopped answering its "
                f"history part-way after {total_msgs} messages"
            )
        elif newest_ts:
            await self.messages_sync_point.update_sync_point(
                sync_key, {"last_sync_time": newest_ts}
            )
            self.logger.info(f"Checkpoint updated for {channel_id}: ts={newest_ts}")

        self.logger.info(f"{total_msgs} messages synced for {channel_id}")

    async def _fetch_thread_replies_raw(
        self,
        channel_id: str,
        thread_ts: str,
        ctx: Any,  # noqa: ANN401 - the connector's ProcessingContext
        oldest: Optional[str] = None,
    ) -> tuple[list[dict[str, Any]], bool | None]:
        """Fetch raw thread replies (excluding the parent) via conversations.replies.

        Args:
            oldest: If set, only replies with ts > oldest are returned
                    (Slack ``oldest`` is exclusive lower bound).

        Returns:
            ``(replies, complete)``. ``complete`` is False when a page failed
            before the cursor ran out; ``replies`` then holds only the pages
            fetched so far, and the caller must not record the thread as synced.
            It is None, with no replies, when Slack refused the first page
            (thread_not_found, channel_not_found, not_in_channel).
        """
        replies: list[dict[str, Any]] = []
        cursor:  Optional[str] = None
        seen:    set[str] = set()

        while True:
            await ctx.rate_limiter.acquire(Tier.T3)
            ds   = await self._fresh_datasource()
            kwargs: dict[str, Any] = {
                "channel": channel_id, "ts": thread_ts,
                "limit": self._replies_page_size, "cursor": cursor,
            }
            if oldest:
                kwargs["oldest"] = oldest
            resp = await ds.conversations_replies(**kwargs)
            if not resp or not resp.success:
                error = getattr(resp, "error", "no response")
                # Refused before any page was read: not an empty thread, so the
                # caller keeps what is stored. After a page the replies are partial.
                if cursor is None and error in ("thread_not_found", "channel_not_found", "not_in_channel"):
                    self.logger.warning(
                        f"Skipping replies of thread ts={thread_ts} in {channel_id}: {error}"
                    )
                    return replies, None
                self.logger.warning(
                    f"conversations.replies({channel_id}, ts={thread_ts}) failed: "
                    f"{error}; returning {len(replies)} replies fetched so far as incomplete"
                )
                return replies, False

            for md in resp.data.get("messages", []):
                ts = md.get("ts")
                if not ts or ts == thread_ts or ts in seen:
                    continue
                seen.add(ts)
                replies.append(md)

            cursor = resp.data.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                break

        return replies, True

    async def _keep_unread_channels(self) -> None:
        """Note the stored records and thread groups of the listed channels whose history
        Slack refused as kept by the full sync in progress: they may still be in Slack.

        Thread groups cannot be listed per channel, so the connector's records are scanned
        once. The scan does not wait for a record directly in the channel: thread growth
        writes a thread group and its records without one. A lookup that fails marks the
        read incomplete, so nothing is unlinked.
        """
        processor = self.data_entities_processor
        if not self._kept_channel_ids or processor.full_sync_running(self.connector_id) is not True:
            return
        try:
            thread_groups: set[str] = set()
            page_size = 1000
            after_key: Optional[str] = None
            while True:
                page = await processor.get_records_by_status(
                    self.connector_id, None, limit=page_size, after_key=after_key,
                    visibility=RecordVisibility.ALL,
                )
                kept = [
                    r for r in page
                    if r.external_record_group_id and self._channel_ext_id(r.external_record_group_id) in self._kept_channel_ids
                    and not self._read_this_run(r)
                ]
                processor.note_kept_records(self.connector_id, [r.id for r in kept])
                thread_groups.update(
                    r.external_record_group_id for r in kept if r.external_record_group_id.startswith("thread_")
                )
                if len(page) < page_size:
                    break
                after_key = page[-1].id
            for external_group_id in thread_groups:
                await processor.note_kept_record_group(self.connector_id, external_group_id)
        except Exception as exc:
            self._full_sync_read_failed = True
            self.logger.warning(
                f"Could not keep the stored records of unreadable channels "
                f"{sorted(self._kept_channel_ids)}: {exc}"
            )

    async def _keep_unread_thread(self, thread_ext_group_id: str) -> None:
        """Note the stored records of a thread whose replies Slack refused as kept by the
        full sync in progress. A failed lookup marks the read incomplete and propagates."""
        self.logger.warning(
            f"Replies of {thread_ext_group_id} could not be read; its stored records are kept"
        )
        processor = self.data_entities_processor
        if processor.full_sync_running(self.connector_id) is not True:
            return
        page_size = 1000
        after_key: Optional[str] = None
        try:
            while True:
                page = await processor.get_records_in_record_group(
                    self.connector_id, thread_ext_group_id, page_size, after_key,
                    visibility=RecordVisibility.ALL,
                )
                processor.note_kept_records(self.connector_id, [r.id for r in page])
                if len(page) < page_size:
                    break
                after_key = page[-1].id
        except Exception:
            # Phase 5 callers only log a failed thread, so mark the read incomplete here.
            self._full_sync_read_failed = True
            raise

    def _read_this_run(self, record: Record) -> bool:
        """Whether the pages read before Slack refused a channel part-way cover a stored
        record of it in full. Those pages rewrote it, perhaps under another burst id, so
        the stored copy is obsolete. A record that reaches onto the refused pages is kept,
        though its newer messages may now also sit in a record written this run."""
        group = record.external_record_group_id or ""
        read_from = self._kept_channel_read_from.get(self._channel_ext_id(group))
        if not read_from:
            return False
        if group.startswith("thread_"):
            # The parent's ts: a parent on the read pages had its thread processed.
            ts = group.rsplit("_", 1)[-1]
        else:
            # Its oldest message: bursts and singles set start_ts, other records only the time.
            ts = getattr(record, "start_ts", None)
            if not ts:
                if record.source_created_at is None:
                    return False
                # Whole milliseconds, as source_created_at is stored.
                return record.source_created_at >= int(float(read_from) * 1000)
        return float(ts) >= float(read_from)

    # Threads whose replies Slack refused, read again on later syncs

    def _pending_threads_path(self, channel_id: str) -> str:
        # In the KV store, not a sync point: a full sync deletes every sync point first,
        # and a refused parent older than the sync window would never be read again.
        return f"/services/connectors/{self.connector_id}/slack_pending_threads/{channel_id}"

    async def _read_pending_threads(self, channel_id: str) -> list[str]:
        """Parent ts of the channel's threads whose replies Slack refused, oldest refusal first."""
        data = await self.config_service.get_config(
            self._pending_threads_path(channel_id), use_cache=False, keep_in_cache=False, raise_on_error=True,
        )
        if not isinstance(data, dict):
            return []
        return [str(ts) for ts in data.get("thread_ts") or []]

    async def _write_pending_threads(self, channel_id: str, pending: list[str]) -> None:
        if len(pending) > MAX_PENDING_THREADS_PER_CHANNEL:
            dropped = pending[:-MAX_PENDING_THREADS_PER_CHANNEL]
            pending = pending[-MAX_PENDING_THREADS_PER_CHANNEL:]
            self.logger.warning(
                f"Channel {channel_id}: {len(dropped)} thread(s) whose replies Slack keeps "
                f"refusing are no longer retried (ts {dropped}); a full sync reads them again "
                f"if they are inside the sync window"
            )
        path = self._pending_threads_path(channel_id)
        if pending or not self._delete_empty_pending_threads:
            saved = await self.config_service.set_config(path, {"thread_ts": pending}, keep_in_cache=False)
        else:
            saved = await delete_config_confirmed(self.config_service, path)
        if not saved:
            raise RuntimeError(f"Pending threads of channel {channel_id} could not be saved")

    async def _add_pending_thread(self, channel_id: str, ts: str) -> None:
        pending = await self._read_pending_threads(channel_id)
        if ts not in pending:
            await self._write_pending_threads(channel_id, [*pending, ts])

    async def _retry_pending_thread(self, channel_id: str, ts: str) -> bool:
        """Read again a thread whose replies Slack refused on an earlier sync. True when
        it needs no more retries: its replies were read, or its parent or replies are gone."""
        try:
            await self.rate_limiter.acquire(Tier.T3)
            ds = await self._fresh_datasource()
            resp = await ds.conversations_history(
                channel=channel_id, oldest=ts, latest=ts, inclusive=True, limit=1,
            )
            if not resp or not resp.success:
                self.logger.warning(
                    f"Thread ts={ts} in {channel_id} not retried: "
                    f"{getattr(resp, 'error', 'no response')}"
                )
                return False
            parent = next(
                (m for m in resp.data.get("messages", []) if m.get("ts") == ts), None
            )
            if not parent or not parent.get("reply_count"):
                self.logger.info(
                    f"Thread ts={ts} in {channel_id} has no replies left to read; no longer retried"
                )
                return True
            if self._pending_parent_skipped(channel_id, ts, parent):
                return True
            result = await self._handle_new_thread(parent, channel_id, ts)
            if result is False:
                self._full_sync_read_failed = True
            return result is True
        except Exception as exc:
            self._full_sync_read_failed = True
            self.logger.error(
                f"Retry of thread ts={ts} in {channel_id} failed: {exc}", exc_info=True
            )
            return False

    def _pending_parent_skipped(self, channel_id: str, ts: str, parent: dict[str, Any]) -> bool:
        """Whether a pending thread whose parent is still there needs no more retries
        for a reason of the connector's own."""
        return False

    @staticmethod
    def _channel_ext_id(external_group_id: str) -> str:
        """The Slack channel id behind a record's external group id.

        Threaded records carry the thread group's id, ``thread_{channel}_{ts}``.
        """
        if external_group_id.startswith("thread_"):
            parts = external_group_id.split("_", 2)  # ["thread", channel_id, thread_ts]
            if len(parts) >= 2:
                return parts[1]
        return external_group_id
