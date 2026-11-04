"""GmailMailboxSync against an in-memory mailbox, store and sync point.

The rules under test (module docstring of gmail_mailbox_sync): the sync point moves only past stored work, a full
read resumes where it stopped, unread threads and messages are read again on the next sync, trash and spam are out
of scope, and an expired history leads to a full read that removes what Gmail no longer has.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest
from _gmail_errors_fixtures import http_error
from googleapiclient.errors import HttpError

from app.connectors.sources.google.common.gmail_call_policy import GmailCallPolicy
from app.connectors.sources.google.common.gmail_mailbox_sync import (
    GmailMailboxApi,
    GmailMailboxSync,
    MailboxWindow,
)
from app.connectors.sources.google.common.gmail_sync_health import GmailSyncHealth

MAILBOX = "a@x.example"


class FakeGmail:
    """One mailbox: threads of messages, a history log, and failures to inject per call."""

    def __init__(self) -> None:
        self.messages: dict[str, dict[str, Any]] = {}
        self.history: list[dict[str, Any]] = []
        self.history_id = 100
        self.min_history = 0
        self.fail: dict[str, list[BaseException]] = {}
        self.page_size = 2

    def add(self, mid: str, tid: str, labels: list[str], date: int) -> None:
        self.messages[mid] = {"id": mid, "threadId": tid, "labelIds": labels, "internalDate": str(date)}
        self.history_id += 3
        self.history.append({"id": str(self.history_id), "messagesAdded": [{"message": dict(self.messages[mid])}]})

    def relabel(self, mid: str, labels: list[str]) -> None:
        self.messages[mid]["labelIds"] = labels
        self.history_id += 3
        self.history.append({"id": str(self.history_id),
                             "labelsAdded": [{"message": dict(self.messages[mid]), "labelIds": labels}]})

    def delete(self, mid: str) -> None:
        gone = self.messages.pop(mid)
        self.history_id += 3
        self.history.append({"id": str(self.history_id), "messagesDeleted": [{"message": gone}]})

    def _maybe_fail(self, name: str) -> None:
        if self.fail.get(name):
            raise self.fail[name].pop(0)

    async def users_get_profile(self, userId: str) -> dict[str, Any]:
        return {"emailAddress": MAILBOX, "historyId": str(self.history_id)}

    async def users_threads_list(self, userId: str, maxResults: int, pageToken: str | None, q: str | None) -> dict:
        self._maybe_fail("threads.list")
        threads = sorted({m["threadId"] for m in self.messages.values()
                          if not set(m["labelIds"]) & {"TRASH", "SPAM"}})
        start = int(pageToken or 0)
        page = threads[start:start + self.page_size]
        out: dict[str, Any] = {"threads": [{"id": t} for t in page]}
        if start + self.page_size < len(threads):
            out["nextPageToken"] = str(start + self.page_size)
        return out

    async def users_threads_get(self, userId: str, **kwargs) -> dict:
        thread_id = kwargs["id"]
        self._maybe_fail(f"threads.get:{thread_id}")
        msgs = [dict(m) for m in self.messages.values() if m["threadId"] == thread_id]
        if not msgs:
            raise http_error(404, "notFound")
        return {"id": thread_id, "messages": msgs}

    async def users_messages_get(self, userId: str, **kwargs) -> dict:
        message_id = kwargs["id"]
        self._maybe_fail(f"messages.get:{message_id}")
        if message_id not in self.messages:
            raise http_error(404, "notFound")
        return dict(self.messages[message_id])

    async def users_history_list(self, userId: str, startHistoryId: str, historyTypes: list[str], maxResults: int,
                                 pageToken: str | None) -> dict:
        self._maybe_fail("history.list")
        if int(startHistoryId) < self.min_history:
            raise http_error(404, "notFound")
        records = [r for r in self.history if int(r["id"]) > int(startHistoryId)]
        return {"historyId": str(self.history_id), **({"history": records} if records else {})}


class FakeStore:
    """The parts of DataSourceEntitiesProcessor the mailbox sync uses, over a dict of records."""

    def __init__(self) -> None:
        self.records: dict[str, SimpleNamespace] = {}
        self.siblings: list[tuple[str, str]] = []
        self.fail_writes = 0
        self.writes = 0

    async def get_record_by_external_id(self, connector_id: str, external_id: str) -> SimpleNamespace | None:
        return self.records.get(external_id)

    async def on_new_records(self, batch: list, replace_permissions: bool = True) -> None:
        if self.fail_writes:
            self.fail_writes -= 1
            raise RuntimeError("store down")
        self.writes += 1
        for record, _ in batch:
            self.records[record.external_record_id] = record

    async def on_record_metadata_update(self, record: SimpleNamespace) -> None:
        self.records[record.external_record_id] = record

    async def create_record_relation(self, a: str, b: str, kind: str) -> None:
        ids = {r.id for r in self.records.values()}
        assert a in ids and b in ids, "a link is written only between stored records"
        self.siblings.append((a, b))

    async def get_records_by_parent(self, connector_id: str, parent: str, record_type: str) -> list:
        return [r for r in self.records.values() if getattr(r, "parent", None) == parent]

    async def on_record_deleted(self, record_id: str) -> None:
        for ext, record in list(self.records.items()):
            if record.id == record_id:
                del self.records[ext]

    async def get_records_in_record_group(self, connector_id: str, group: str, limit: int,
                                          after: str | None = None) -> list:
        page = sorted((r for r in self.records.values() if r.external_record_group_id == group), key=lambda r: r.id)
        if after:
            page = [r for r in page if r.id > after]
        return page[:limit]


class FakeSyncPoint:
    def __init__(self) -> None:
        self.data: dict[str, dict] = {}

    async def read_sync_point(self, key: str) -> dict:
        return dict(self.data.get(key, {}))

    async def update_sync_point(self, key: str, value: dict) -> None:
        self.data[key] = dict(value)


async def build_records(message: dict, thread_id: str) -> list:
    labels = message.get("labelIds") or []
    group = f"{MAILBOX}:{'SENT' if 'SENT' in labels else 'INBOX' if 'INBOX' in labels else 'OTHERS'}"
    record = SimpleNamespace(id=f"r-{message['id']}", external_record_id=message["id"], record_type="MAIL",
                             external_record_group_id=group, source_created_at=int(message["internalDate"]))
    return [(record, [])]


class NoWait:
    async def __call__(self, seconds: float) -> None:
        return None


def make(gmail: FakeGmail, store: FakeStore, point: FakeSyncPoint, health: GmailSyncHealth | None = None,
         window: MailboxWindow | None = None) -> GmailMailboxSync:
    async def datasource() -> FakeGmail:
        return gmail

    policy = GmailCallPolicy(logging.getLogger("t"), sleep=NoWait())
    return GmailMailboxSync(mailbox=MAILBOX, api=GmailMailboxApi(datasource, MAILBOX, policy),
                            build_records=build_records, processor=store, connector_id="c", sync_point=point,
                            sync_point_key="k", health=health or GmailSyncHealth(), logger=logging.getLogger("t"),
                            window=window or MailboxWindow(), batch_size=2)


def mailbox_of(n: int) -> FakeGmail:
    gmail = FakeGmail()
    for i in range(n):
        gmail.add(f"m{i}", f"t{i}", ["INBOX"], 1000 + i)
    return gmail


@pytest.mark.asyncio
async def test_first_sync_stores_every_thread_and_sets_the_cursor() -> None:
    gmail, store, point = mailbox_of(5), FakeStore(), FakeSyncPoint()
    await make(gmail, store, point).run()
    assert set(store.records) == {f"m{i}" for i in range(5)}
    assert point.data["k"]["historyId"] == "115" and point.data["k"]["fullSyncHistoryId"] is None


@pytest.mark.asyncio
async def test_an_interrupted_full_read_resumes_without_skipping() -> None:
    gmail, store, point = mailbox_of(6), FakeStore(), FakeSyncPoint()
    calls = {"n": 0}
    original = gmail.users_threads_list

    async def second_page_breaks(**kwargs) -> dict:
        calls["n"] += 1
        if calls["n"] == 2:
            raise http_error(400, "invalidArgument", "Bad Request")
        return await original(**kwargs)

    gmail.users_threads_list = second_page_breaks  # type: ignore[method-assign]
    with pytest.raises(HttpError):
        await make(gmail, store, point).run()
    assert not point.data["k"].get("historyId"), "the cursor is not set while the read is unfinished"
    assert point.data["k"]["fullSyncPageToken"] == "2", "the read records the page it reached after storing it"
    gmail.users_threads_list = original  # type: ignore[method-assign]
    await make(gmail, store, point).run()
    assert set(store.records) == {f"m{i}" for i in range(6)}


@pytest.mark.asyncio
async def test_a_failed_thread_is_read_on_the_next_sync() -> None:
    gmail, store, point = mailbox_of(3), FakeStore(), FakeSyncPoint()
    gmail.fail["threads.get:t1"] = [http_error(400, "invalidArgument")]
    health = GmailSyncHealth()
    await make(gmail, store, point, health).run()
    assert "m1" not in store.records and not health.complete
    assert point.data["k"]["retryThreads"] == ["t1"]
    await make(gmail, store, point).run()
    assert "m1" in store.records and point.data["k"]["retryThreads"] == []


@pytest.mark.asyncio
async def test_incremental_follows_adds_moves_trash_spam_and_deletes() -> None:
    gmail, store, point = mailbox_of(4), FakeStore(), FakeSyncPoint()
    await make(gmail, store, point).run()
    gmail.add("new", "t0", ["Label_3"], 2000)  # a filter skipped the inbox
    gmail.relabel("m1", ["Label_3"])  # archived
    gmail.relabel("m2", ["TRASH"])
    gmail.relabel("m3", ["SPAM"])
    gmail.delete("m0")
    await make(gmail, store, point).run()
    assert "new" in store.records
    assert store.records["m1"].external_record_group_id == f"{MAILBOX}:OTHERS"
    assert not {"m0", "m2", "m3"} & set(store.records)


@pytest.mark.asyncio
async def test_a_history_error_moves_nothing() -> None:
    gmail, store, point = mailbox_of(2), FakeStore(), FakeSyncPoint()
    await make(gmail, store, point).run()
    cursor = point.data["k"]["historyId"]
    gmail.add("late", "t9", ["INBOX"], 3000)
    gmail.fail["history.list"] = [http_error(400, "invalidArgument")]
    with pytest.raises(HttpError):
        await make(gmail, store, point).run()
    assert point.data["k"]["historyId"] == cursor
    await make(gmail, store, point).run()
    assert "late" in store.records


@pytest.mark.asyncio
async def test_a_store_failure_moves_nothing() -> None:
    gmail, store, point = mailbox_of(2), FakeStore(), FakeSyncPoint()
    await make(gmail, store, point).run()
    cursor = point.data["k"]["historyId"]
    gmail.add("late", "t9", ["INBOX"], 3000)
    store.fail_writes = 1
    with pytest.raises(RuntimeError):
        await make(gmail, store, point).run()
    assert point.data["k"]["historyId"] == cursor
    await make(gmail, store, point).run()
    assert "late" in store.records


@pytest.mark.asyncio
async def test_a_failed_message_read_is_kept_and_read_again() -> None:
    gmail, store, point = mailbox_of(1), FakeStore(), FakeSyncPoint()
    await make(gmail, store, point).run()
    gmail.add("late", "t9", ["INBOX"], 3000)
    gmail.fail["messages.get:late"] = [http_error(400, "invalidArgument")]
    await make(gmail, store, point).run()
    assert "late" not in store.records and point.data["k"]["retryMessages"] == ["late"]
    await make(gmail, store, point).run()
    assert "late" in store.records and point.data["k"]["retryMessages"] == []


@pytest.mark.asyncio
async def test_a_failed_thread_keeps_reconciliation_until_the_next_full_read() -> None:
    gmail, store, point = mailbox_of(3), FakeStore(), FakeSyncPoint()
    await make(gmail, store, point).run()
    gmail.delete("m0")
    gmail.min_history = gmail.history_id + 1
    gmail.fail["threads.get:t2"] = [http_error(400, "invalidArgument")]
    await make(gmail, store, point).run()
    assert "m0" in store.records, "a reconciliation that could not read a thread removes nothing"
    assert point.data["k"]["fullSyncReconcile"] is True
    await make(gmail, store, point).run()
    assert "m0" not in store.records and "m2" in store.records
    assert point.data["k"]["fullSyncReconcile"] is False


@pytest.mark.asyncio
async def test_expired_history_reads_again_and_removes_what_is_gone() -> None:
    gmail, store, point = mailbox_of(3), FakeStore(), FakeSyncPoint()
    await make(gmail, store, point).run()
    gmail.delete("m1")
    gmail.add("during", "t7", ["INBOX"], 4000)
    gmail.min_history = gmail.history_id + 1
    await make(gmail, store, point).run()
    assert "during" in store.records and "m1" not in store.records and "m0" in store.records


@pytest.mark.asyncio
async def test_expired_history_keeps_mail_outside_the_window() -> None:
    gmail, store, point = mailbox_of(3), FakeStore(), FakeSyncPoint()
    await make(gmail, store, point).run()
    gmail.min_history = gmail.history_id + 1
    await make(gmail, store, point, window=MailboxWindow(start_ms=1001)).run()
    assert "m0" in store.records, "a record older than the window is not known to be gone"


@pytest.mark.asyncio
async def test_trashed_message_in_a_live_thread_is_not_stored() -> None:
    gmail, store, point = FakeGmail(), FakeStore(), FakeSyncPoint()
    gmail.add("a", "t", ["INBOX"], 1)
    gmail.add("b", "t", ["TRASH"], 2)
    gmail.add("c", "t", ["SENT"], 3)
    await make(gmail, store, point).run()
    assert set(store.records) == {"a", "c"}
    assert store.siblings == [("r-a", "r-c")]


@pytest.mark.asyncio
async def test_old_sync_point_written_mid_read_restarts_the_read() -> None:
    gmail, store, point = mailbox_of(3), FakeStore(), FakeSyncPoint()
    point.data["k"] = {"pageToken": "2", "historyId": "50"}
    await make(gmail, store, point).run()
    assert set(store.records) == {"m0", "m1", "m2"}
    assert point.data["k"]["historyId"] == "50", "the read resumes the history from where the old read began"


@pytest.mark.asyncio
async def test_a_saved_page_token_gmail_refuses_restarts_the_read_once() -> None:
    gmail, store, point = mailbox_of(4), FakeStore(), FakeSyncPoint()
    point.data["k"] = {"fullSyncHistoryId": "100", "fullSyncPageToken": "stale"}
    original = gmail.users_threads_list

    async def refuses_stale(**kwargs) -> dict:
        if kwargs.get("pageToken") == "stale":
            raise http_error(400, "invalidArgument", "Invalid pageToken")
        return await original(**kwargs)

    gmail.users_threads_list = refuses_stale  # type: ignore[method-assign]
    await make(gmail, store, point).run()
    assert set(store.records) == {f"m{i}" for i in range(4)}


@pytest.mark.asyncio
async def test_a_page_refused_mid_read_fails_the_mailbox_instead_of_looping() -> None:
    gmail, store, point = mailbox_of(6), FakeStore(), FakeSyncPoint()
    original = gmail.users_threads_list

    async def page_two_always_refused(**kwargs) -> dict:
        if kwargs.get("pageToken"):
            raise http_error(400, "invalidArgument", "Bad Request")
        return await original(**kwargs)

    gmail.users_threads_list = page_two_always_refused  # type: ignore[method-assign]
    with pytest.raises(HttpError):
        await make(gmail, store, point).run()
