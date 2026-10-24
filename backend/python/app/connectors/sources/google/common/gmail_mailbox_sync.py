"""Syncs one Gmail mailbox into PipesHub: a full read, or the changes since the last sync.

One job: keep one mailbox's records in step with Gmail without losing anything. The Workspace and the personal
connector both use it; each passes in how to reach the mailbox (`GmailMailboxApi`), how to turn a message into
records (`build_records`), the store, the sync point and where to report failures.

The rules it keeps:
- The sync point moves only past work that was written. A full read records where it stands only after the
  page's records are stored, and resumes from there. Threads and messages that could not be read are kept in the
  sync point and read again on the next sync, so a failure delays them and never drops them.
- History is read without a label filter, so mail a filter archives, mail under a label and mail leaving the
  inbox are all seen (`gmail_history_changes`).
- Trash and spam are out of scope, as users.threads.list leaves them out: a message moved there is removed, and
  a trashed message inside a live thread is not synced.
- Gmail drops history after about a week (users.history.list answers 404). The mailbox is then read in full and,
  once that read completed without errors, the records of messages it did not find are removed.
- Nothing is removed on an error or an empty answer: only on Gmail saying the message is gone or out of scope.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import RecordRelations, RecordTypes
from app.connectors.sources.google.common.gmail_errors import GmailFailure, classify
from app.connectors.sources.google.common.gmail_history_changes import (
    HISTORY_TYPES,
    OUT_OF_SCOPE_LABELS,
    MessageState,
    fold_history,
)
from app.models.entities import Record
from app.models.permission import Permission
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from logging import Logger

    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )
    from app.connectors.core.base.sync_point.sync_point import SyncPoint
    from app.connectors.sources.google.common.gmail_call_policy import GmailCallPolicy
    from app.connectors.sources.google.common.gmail_sync_health import GmailSyncHealth

BuildRecords = Callable[[dict[str, Any], str], Awaitable[list[tuple[Record, list[Permission]]]]]
MAILBOX_GROUPS = ("INBOX", "SENT", "OTHERS")
_RECONCILE_PAGE = 500


class HistoryExpired(Exception):
    """users.history.list no longer has the history the sync point starts from."""


class GmailMailboxApi:
    """The Gmail reads a mailbox sync makes, through the retry policy.

    `datasource` returns the data source to call (the personal connector refreshes its credentials there);
    `user_id` is the mailbox's address for a delegated Workspace client, or "me"."""

    def __init__(self, datasource: Callable[[], Awaitable[Any]], user_id: str, policy: GmailCallPolicy) -> None:
        self._datasource = datasource
        self._user_id = user_id
        self._policy = policy

    async def _run(self, what: str, call: Callable[[Any], Awaitable[Any]]) -> Any:  # noqa: ANN401 - Gmail's JSON
        async def attempt() -> Any:  # noqa: ANN401
            return await call(await self._datasource())

        return await self._policy.run(attempt, what)

    async def profile(self) -> dict[str, Any]:
        return await self._run("profile", lambda ds: ds.users_get_profile(userId=self._user_id))

    async def list_threads(self, page_token: str | None, q: str | None) -> dict[str, Any]:
        return await self._run("threads.list", lambda ds: ds.users_threads_list(
            userId=self._user_id, maxResults=100, pageToken=page_token, q=q))

    async def get_thread(self, thread_id: str, fmt: str = "full") -> dict[str, Any]:
        return await self._run("threads.get", lambda ds: ds.users_threads_get(
            userId=self._user_id, id=thread_id, format=fmt))

    async def get_message(self, message_id: str) -> dict[str, Any]:
        return await self._run("messages.get", lambda ds: ds.users_messages_get(
            userId=self._user_id, id=message_id, format="full"))

    async def list_history(self, start: str, page_token: str | None) -> dict[str, Any]:
        try:
            return await self._run("history.list", lambda ds: ds.users_history_list(
                userId=self._user_id, startHistoryId=start, historyTypes=HISTORY_TYPES, maxResults=500,
                pageToken=page_token))
        except Exception as error:
            if classify(error) is GmailFailure.NOT_FOUND:
                raise HistoryExpired(str(start)) from error
            raise


@dataclass
class MailboxWindow:
    """The Received Date sync filter: the `q` for threads.list and the bounds for mail found through history."""

    q: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None

    def holds(self, internal_ms: int | None) -> bool:
        if internal_ms is None:
            return True
        if self.start_ms is not None and internal_ms < self.start_ms:
            return False
        return self.end_ms is None or internal_ms <= self.end_ms


class GmailMailboxSync:
    def __init__(
        self,
        *,
        mailbox: str,
        api: GmailMailboxApi,
        build_records: BuildRecords,
        processor: DataSourceEntitiesProcessor,
        connector_id: str,
        sync_point: SyncPoint,
        sync_point_key: str,
        health: GmailSyncHealth,
        logger: Logger,
        window: MailboxWindow,
        batch_size: int = 100,
    ) -> None:
        self.mailbox = mailbox
        self.api = api
        self.build_records = build_records
        self.processor = processor
        self.connector_id = connector_id
        self.sync_point = sync_point
        self.key = sync_point_key
        self.health = health
        self.logger = logger
        self.window = window
        self.batch_size = batch_size
        self._batch: list[tuple[Record, list[Permission]]] = []
        self._siblings: list[tuple[str, str]] = []
        self.stats = {"threads": 0, "messages": 0, "deleted": 0, "moved": 0}

    # ---- sync point --------------------------------------------------------------------------------------

    async def _load(self) -> dict[str, Any]:
        """The sync point as plain fields (a graph store keeps only scalars and lists of them):
        historyId, fullSyncHistoryId / fullSyncPageToken / fullSyncReconcile while a full read is under way,
        retryThreads and retryMessages."""
        state = dict(await self.sync_point.read_sync_point(self.key) or {})
        if state.get("pageToken") and not state.get("fullSyncHistoryId"):
            # Written by the old code part-way through a full read: its pages are not known to have been
            # stored, so the read starts again from the history id it began at.
            state.update({"fullSyncHistoryId": state.get("historyId"), "fullSyncPageToken": None,
                          "fullSyncReconcile": False, "historyId": None})
        state["pageToken"] = None
        return state

    async def _save(self, state: dict[str, Any]) -> None:
        state["lastSyncTimestamp"] = get_epoch_timestamp_in_ms()
        await self.sync_point.update_sync_point(self.key, state)

    # ---- entry point -------------------------------------------------------------------------------------

    async def run(self) -> None:
        state = await self._load()
        # fullSyncReconcile stays set until a complete listing from the start has removed what it did not
        # see. A resumed or partly failed read must not hand the mailbox to incremental sync, which never does
        # that removal.
        if state.get("fullSyncHistoryId") or state.get("fullSyncReconcile") or not state.get("historyId"):
            await self._full(state)
            return
        try:
            await self._incremental(state)
        except HistoryExpired:
            self.logger.info(f"Gmail history for {self.mailbox} has expired; reading the mailbox again in full")
            state.update({"fullSyncHistoryId": None, "fullSyncPageToken": None, "fullSyncReconcile": True})
            await self._save(state)
            await self._full(state)

    # ---- full read ---------------------------------------------------------------------------------------

    async def _full(self, state: dict[str, Any]) -> None:
        if not state.get("fullSyncHistoryId"):
            profile = await self.api.profile()
            state.update({"fullSyncHistoryId": str(profile["historyId"]), "fullSyncPageToken": None})
            await self._save(state)
        reconcile = bool(state.get("fullSyncReconcile"))
        listed_from_start = not state.get("fullSyncPageToken")
        seen: set[str] = set()
        # A listing from the start sees every thread that still exists. Keeping an older retry for a thread
        # that is gone would block reconciliation forever.
        failed_threads: set[str] = set() if listed_from_start else set(state.get("retryThreads") or [])
        page_token = state.get("fullSyncPageToken")
        saved_token = page_token
        while True:
            try:
                response = await self.api.list_threads(page_token, self.window.q)
            except Exception as error:
                if saved_token and page_token == saved_token and classify(error) is GmailFailure.OTHER:
                    # The page token an earlier sync saved is no longer accepted: list once more from the start.
                    self.logger.info(f"Gmail did not accept the saved page token for {self.mailbox}; restarting")
                    page_token, saved_token, listed_from_start, seen, failed_threads = (
                        None, None, True, set(), set())
                    continue
                raise
            for thread in response.get("threads") or []:
                tid = thread.get("id")
                if not tid:
                    continue
                if await self._read_thread(tid, seen):
                    failed_threads.discard(tid)
                else:
                    failed_threads.add(tid)
            await self._flush()
            page_token = response.get("nextPageToken")
            if not page_token:
                break
            state["fullSyncPageToken"] = page_token
            state["retryThreads"] = sorted(failed_threads)
            await self._save(state)
        reconciled = (not reconcile) or (listed_from_start and not failed_threads)
        if reconcile and reconciled:
            await self._remove_unseen(seen)
        state.update({"historyId": state["fullSyncHistoryId"], "fullSyncHistoryId": None, "fullSyncPageToken": None,
                      "fullSyncReconcile": not reconciled, "retryThreads": sorted(failed_threads)})
        await self._save(state)

    async def _read_thread(self, thread_id: str, seen: set[str] | None = None) -> bool:
        """Stores a thread's in-scope messages. False when it could not be read (kept for a later sync)."""
        try:
            thread = await self.api.get_thread(thread_id, "full")
        except Exception as error:
            if classify(error) is GmailFailure.NOT_FOUND:
                return True
            self.health.record(self.mailbox, error, f"thread {thread_id}")
            self.logger.warning(f"Could not read Gmail thread {thread_id} of {self.mailbox}; it will be read again "
                                f"on the next sync: {error}")
            return False
        self.stats["threads"] += 1
        previous: str | None = None
        for message in sorted(thread.get("messages") or [], key=lambda m: int(m.get("internalDate") or 0)):
            mid = message.get("id")
            if not mid:
                continue
            if set(message.get("labelIds") or []) & OUT_OF_SCOPE_LABELS:
                await self._remove_message(mid)
                continue
            if seen is not None:
                seen.add(mid)
            built = await self._add_message(message, thread_id, previous)
            if built:
                previous = built
        return True

    async def _add_message(self, message: dict[str, Any], thread_id: str, previous: str | None) -> str | None:
        """Queues a message and its attachments; returns the message record's id."""
        records = await self.build_records(message, thread_id)
        if not records:
            return None
        mail_id = records[0][0].id
        self._batch.extend(records)
        self.stats["messages"] += 1
        if previous:
            self._siblings.append((previous, mail_id))
        if len(self._batch) >= self.batch_size:
            await self._flush()
        return mail_id

    async def _flush(self) -> None:
        """Writes the queued records, then the links between them (a link needs both records stored)."""
        if self._batch:
            batch, self._batch = self._batch, []
            await self.processor.on_new_records(batch, replace_permissions=True)
        if self._siblings:
            siblings, self._siblings = self._siblings, []
            for previous, current in siblings:
                await self.processor.create_record_relation(previous, current, RecordRelations.SIBLING.value)
        await asyncio.sleep(0)

    # ---- changes since the last sync ---------------------------------------------------------------------

    async def _incremental(self, state: dict[str, Any]) -> None:
        start = str(state["historyId"])
        records: list[dict[str, Any]] = []
        current: str | None = None
        page_token: str | None = None
        while True:
            response = await self.api.list_history(start, page_token)
            # The mailbox's history id when the read began: changes after it come on the next sync.
            current = current or response.get("historyId")
            records.extend(response.get("history") or [])
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        changes = fold_history(records)
        for mid in state.get("retryMessages") or []:
            changes.present.setdefault(mid, MessageState(mid, None, None))
        failed_threads = set()
        for tid in state.get("retryThreads") or []:
            if not await self._read_thread(tid):
                failed_threads.add(tid)
        await self._flush()
        for mid in sorted(changes.deleted):
            await self._remove_message(mid)
        failed_messages: set[str] = set()
        for mid, change in changes.present.items():
            if not await self._apply_change(change):
                failed_messages.add(mid)
        await self._flush()
        state.update({"historyId": str(current or changes.last_history_id or start),
                      "retryThreads": sorted(failed_threads), "retryMessages": sorted(failed_messages)})
        await self._save(state)

    async def _apply_change(self, change: MessageState) -> bool:
        """Brings one message's record in line with Gmail. False when Gmail could not be read (kept for later)."""
        existing = await self.processor.get_record_by_external_id(self.connector_id, change.message_id)
        labels = change.labels
        message: dict[str, Any] | None = None
        moved = existing is not None and labels is not None and \
            existing.external_record_group_id != self._group_for(labels)
        if labels is None or moved or (existing is None and change.in_scope):
            try:
                message = await self.api.get_message(change.message_id)
            except Exception as error:
                if classify(error) is GmailFailure.NOT_FOUND:
                    await self._remove_message(change.message_id)
                    return True
                self.health.record(self.mailbox, error, f"message {change.message_id}")
                self.logger.warning(f"Could not read Gmail message {change.message_id} of {self.mailbox}; it will "
                                    f"be read again on the next sync: {error}")
                return False
            labels = tuple(message.get("labelIds") or [])
        if set(labels) & OUT_OF_SCOPE_LABELS:
            await self._remove_message(change.message_id)
            return True
        thread_id = (message or {}).get("threadId") or change.thread_id or change.message_id
        if existing is not None:
            if message is not None and existing.external_record_group_id != self._group_for(labels):
                await self._move(message, thread_id)
            return True
        if message is None:
            return True
        internal = message.get("internalDate")
        if not self.window.holds(int(internal) if internal else None):
            return True
        order = await self._thread_order(thread_id)
        if order is not None and await self._thread_has_unsynced(order, change.message_id):
            # Earlier messages of this thread were never synced (the thread moved while the first read was
            # paging through the list, say): read the whole thread.
            return await self._read_thread(thread_id)
        previous = await self._previous_in_thread(order or [], change.message_id, internal)
        await self._add_message(message, thread_id, previous)
        return True

    async def _thread_order(self, thread_id: str) -> list[dict[str, Any]] | None:
        """The thread's messages (id, date, labels) in the minimal format: a third of the quota of a full read."""
        try:
            thread = await self.api.get_thread(thread_id, "minimal")
        except Exception as error:
            self.logger.debug(f"Could not read thread {thread_id} for its order: {error}")
            return None
        return list(thread.get("messages") or [])

    async def _thread_has_unsynced(self, order: list[dict[str, Any]], message_id: str) -> bool:
        for item in order:
            mid = item.get("id")
            if not mid or mid == message_id or set(item.get("labelIds") or []) & OUT_OF_SCOPE_LABELS:
                continue
            if not self.window.holds(int(item["internalDate"]) if item.get("internalDate") else None):
                continue
            if any(r.external_record_id == mid for r, _ in self._batch):
                continue
            if await self.processor.get_record_by_external_id(self.connector_id, mid) is None:
                return True
        return False

    async def _previous_in_thread(self, order: list[dict[str, Any]], message_id: str,
                                  internal: str | None) -> str | None:
        """The record id of the message before this one in its thread, for the sibling link."""
        date = int(internal or 0)
        earlier = sorted((int(m.get("internalDate") or 0), m.get("id")) for m in order
                         if m.get("id") != message_id and int(m.get("internalDate") or 0) <= date)
        for _, mid in reversed(earlier):
            queued = next((r.id for r, _ in self._batch if r.external_record_id == mid), None)
            if queued:
                return queued
            stored = await self.processor.get_record_by_external_id(self.connector_id, mid)
            if stored is not None:
                return stored.id
        return None

    def _group_for(self, labels: tuple[str, ...]) -> str:
        if "SENT" in labels:
            return f"{self.mailbox}:SENT"
        if "INBOX" in labels:
            return f"{self.mailbox}:INBOX"
        return f"{self.mailbox}:OTHERS"

    async def _move(self, message: dict[str, Any], thread_id: str) -> None:
        """A message that left or joined INBOX/SENT moves to that folder group, with its attachments. Its records
        are rebuilt from the message (the store returns untyped records) and saved as a metadata change, so
        nothing is indexed again."""
        await self._flush()
        for record, _ in await self.build_records(message, thread_id):
            await self.processor.on_record_metadata_update(record)
        self.stats["moved"] += 1

    async def _remove_message(self, message_id: str) -> None:
        record = await self.processor.get_record_by_external_id(self.connector_id, message_id)
        if record is None:
            return
        for attachment in await self.processor.get_records_by_parent(self.connector_id, message_id,
                                                                      RecordTypes.FILE.value):
            await self.processor.on_record_deleted(attachment.id)
        await self.processor.on_record_deleted(record.id)
        self.stats["deleted"] += 1

    async def _remove_unseen(self, seen: set[str]) -> None:
        """After a complete full read: the mailbox's message records Gmail no longer lists (and that are inside
        the date window) are gone at the source."""
        for label in MAILBOX_GROUPS:
            after: str | None = None
            while True:
                page = await self.processor.get_records_in_record_group(
                    self.connector_id, f"{self.mailbox}:{label}", _RECONCILE_PAGE, after)
                if not page:
                    break
                for record in page:
                    if getattr(record.record_type, "value", record.record_type) != RecordTypes.MAIL.value:
                        continue
                    if record.external_record_id in seen:
                        continue
                    if not self.window.holds(record.source_created_at):
                        continue
                    await self._remove_message(record.external_record_id)
                if len(page) < _RECONCILE_PAGE:
                    break
                after = page[-1].id
