"""Record events go into the write's own transaction where the graph and the broker are one PostgreSQL database.

There they are delivered exactly when the write commits: a crash after the commit cannot lose them, and a write that
rolls back sends none. On any other pair of stores nothing changes: the processor publishes after the commit.
"""

import logging
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphTransactionStore
from app.services.messaging.postgres.producer import PgMessagingProducer
from app.services.messaging.postgres.queue import PgQueue

MESSAGES = [("r1", {"eventType": "newRecord", "timestamp": 1, "payload": {"recordId": "r1"}})]


class _Tx:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.statements: list[str] = []
        self.before_commit: list = []

    async def commit(self) -> None:
        for step in self.before_commit:
            await step(self)

    async def fetch(self, sql: str, *args: object) -> list[dict]:
        self.statements.append(sql)
        return [{"group_id": "indexing"}]

    async def execute(self, sql: str, *args: object) -> str:
        if self.fail:
            raise RuntimeError("could not serialize access")
        self.statements.append(sql)
        return "INSERT 0 1"


class _PgLikeProvider:
    """The one hook the store needs from the PostgreSQL provider: a function run on the open transaction."""

    def __init__(self, tx: _Tx) -> None:
        self.logger = logging.getLogger("t")
        self.tx = tx
        self.ran_in: list[str] = []

    async def run_in_transaction(self, transaction: str, fn: Callable[[_Tx], Awaitable[object]]) -> object:
        self.ran_in.append(transaction)
        return await fn(self.tx)


def _pg_producer() -> tuple[PgMessagingProducer, MagicMock]:
    runtime = MagicMock()  # the queue's own unit: must never be opened for a publish joined to the write
    return PgMessagingProducer(logging.getLogger("t"), queue=PgQueue(runtime)), runtime


@pytest.mark.parametrize("scope_fields", ["off", "on"])  # on: the store reaches the provider through its write guard
async def test_on_postgres_the_events_are_written_by_the_graph_transaction(monkeypatch: pytest.MonkeyPatch, scope_fields: str) -> None:
    monkeypatch.setenv("MESSAGE_BROKER", "postgres")
    monkeypatch.setenv("ACL_SCOPE_FIELDS", scope_fields)
    provider = _PgLikeProvider(_Tx())
    producer, runtime = _pg_producer()
    store = GraphTransactionStore(provider, "txn-1")
    assert store.tracked is (scope_fields == "on")

    assert await store.publish_with_commit(producer, "record-events", MESSAGES) is True
    # nothing yet: the group read (FOR SHARE) and the insert run right before COMMIT, not for the rest of the write
    assert provider.tx.statements == []
    await provider.tx.commit()

    assert provider.ran_in == ["txn-1"]
    assert any("queue.publish" in s for s in provider.tx.statements)
    assert any("pg_notify" in s for s in provider.tx.statements)
    runtime.db.assert_not_called()


async def test_a_failed_insert_fails_the_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MESSAGE_BROKER", "postgres")
    producer, _ = _pg_producer()
    provider = _PgLikeProvider(_Tx(fail=True))
    await GraphTransactionStore(provider, "txn-1").publish_with_commit(producer, "record-events", MESSAGES)
    with pytest.raises(RuntimeError, match="serialize"):
        await provider.tx.commit()


async def test_a_transaction_that_is_gone_fails_the_block_instead_of_reading_as_not_acked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MESSAGE_BROKER", "postgres")
    producer, _ = _pg_producer()
    provider = _PgLikeProvider(_Tx())
    provider.run_in_transaction = AsyncMock(side_effect=RuntimeError("the transaction was rolled back"))
    with pytest.raises(RuntimeError, match="rolled back"):
        await GraphTransactionStore(provider, "txn-1").publish_with_commit(producer, "record-events", MESSAGES)


async def test_a_producer_that_publishes_on_its_own_fails_the_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """It would deliver for a write that can still roll back."""
    monkeypatch.setenv("MESSAGE_BROKER", "postgres")
    producer = MagicMock(send_messages=AsyncMock(return_value=[True]))
    with pytest.raises(RuntimeError, match="not written into the transaction"):
        await GraphTransactionStore(_PgLikeProvider(_Tx()), "txn-1").publish_with_commit(producer, "record-events", MESSAGES)


@pytest.mark.parametrize("broker", ["redis", "kafka"])
async def test_another_broker_publishes_after_the_commit(monkeypatch: pytest.MonkeyPatch, broker: str) -> None:
    monkeypatch.setenv("MESSAGE_BROKER", broker)
    producer = MagicMock(send_messages=AsyncMock())
    assert await GraphTransactionStore(_PgLikeProvider(_Tx()), "txn-1").publish_with_commit(producer, "record-events", MESSAGES) is False
    producer.send_messages.assert_not_awaited()


async def test_a_graph_store_without_a_shared_transaction_publishes_after_the_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neo4j and ArangoDB: the broker is another system, so the event cannot commit with the write."""
    monkeypatch.setenv("MESSAGE_BROKER", "postgres")
    provider = MagicMock(spec=["logger", "commit_transaction"])
    producer = MagicMock(send_messages=AsyncMock())
    assert await GraphTransactionStore(provider, "txn-1").publish_with_commit(producer, "record-events", MESSAGES) is False
    producer.send_messages.assert_not_awaited()


def _processor() -> DataSourceEntitiesProcessor:
    proc = DataSourceEntitiesProcessor(MagicMock(), MagicMock(), AsyncMock())
    proc.messaging_producer = MagicMock(send_messages=AsyncMock(return_value=[True]), send_message=AsyncMock(return_value=True))
    proc._mark_queued_after_publish = AsyncMock()
    proc._publish_record_titles = AsyncMock()
    proc._publish_membership_sync = AsyncMock()
    return proc


@pytest.mark.parametrize("published", [True, False])
async def test_a_committed_slice_publishes_only_what_its_transaction_did_not(published: bool) -> None:
    proc = _processor()
    record = MagicMock(id="r1", indexing_status="NOT_STARTED", is_internal=False, is_placeholder=False, origin="CONNECTOR")
    proc._record_event_payload = MagicMock(return_value={"recordId": "r1"})

    await proc._after_slice_committed([record], [], [], {}, published=published)

    assert proc.messaging_producer.send_messages.await_count == (0 if published else 1)
    proc._mark_queued_after_publish.assert_awaited_once_with(["r1"])


@pytest.mark.parametrize("published", [True, False])
async def test_delete_events_the_transaction_carried_are_not_sent_again(published: bool) -> None:
    proc = _processor()
    event_data = {"payloads": [{"recordId": "r1", "virtualRecordId": "v1"}, "malformed"]}

    unpublished = await proc._publish_delete_events(event_data, published=published)

    assert unpublished == ["malformed"]
    assert proc.messaging_producer.send_message.await_count == (0 if published else 1)
    assert [k for k, _ in proc._delete_messages(event_data)] == ["r1"]
