"""A write conflict (ArangoDB errorNum 1200) reaches the code that runs the write again.

Methods under ``@retry_on_deadlock`` that caught every error and returned False hid
the conflict from the decorator, so a write another writer had blocked was never
run again. They now let it through, as ``_must_propagate`` handlers already do.
"""

from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import (
    GraphDataStore,
    GraphTransactionStore,
    is_write_conflict,
)
from app.connectors.sources.localKB.push.service import KbPushService
from app.models.permission import PermissionType
from app.services.graph_db.arango.arango_http_client import ArangoQueryError

CONFLICT = "Batch insert failed with 1 error(s): Item 0: [1200] timeout waiting to lock key"


@pytest.fixture(autouse=True)
def no_backoff() -> Iterator[None]:
    with patch("app.connectors.core.base.data_store.graph_data_store.asyncio.sleep", new=AsyncMock()):
        yield


def _processor(tx_store: MagicMock) -> DataSourceEntitiesProcessor:
    store = MagicMock(spec=GraphDataStore)
    store.is_transient_error = MagicMock(side_effect=lambda e, **_: is_write_conflict(e))
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=tx_store)
    ctx.__aexit__ = AsyncMock(return_value=False)
    store.transaction = MagicMock(return_value=ctx)
    proc = DataSourceEntitiesProcessor(MagicMock(), store, AsyncMock())
    proc.org_id = "org-1"
    proc.messaging_producer = AsyncMock()
    return proc


def test_a_batch_lock_timeout_is_a_write_conflict() -> None:
    assert is_write_conflict(RuntimeError(CONFLICT))
    assert not is_write_conflict(RuntimeError("unique constraint violated - [1210]"))


async def test_a_group_delete_that_lost_to_another_writer_runs_again() -> None:
    tx = AsyncMock()
    group = SimpleNamespace(id="rg-1", name="Team")
    tx.get_record_group_by_external_id = AsyncMock(side_effect=[RuntimeError(CONFLICT), group])
    proc = _processor(tx)
    proc._collect_group_subtree = AsyncMock(return_value=(["rg-1"], [], {}))
    proc._access_changed = AsyncMock()

    with patch(
        "app.connectors.core.base.data_processor.data_source_entities_processor.is_soft_delete_enabled",
        new=AsyncMock(return_value=False),
    ):
        assert await proc.on_record_group_deleted("ext-1", "conn-1") is True

    assert tx.get_record_group_by_external_id.await_count == 2
    tx.delete_nodes_and_edges.assert_awaited_once()


async def test_a_group_delete_that_failed_otherwise_still_answers_false() -> None:
    tx = AsyncMock()
    tx.get_record_group_by_external_id = AsyncMock(side_effect=RuntimeError("bad query"))
    proc = _processor(tx)

    with patch(
        "app.connectors.core.base.data_processor.data_source_entities_processor.is_soft_delete_enabled",
        new=AsyncMock(return_value=False),
    ):
        assert await proc.on_record_group_deleted("ext-1", "conn-1") is False
    assert tx.get_record_group_by_external_id.await_count == 1


async def test_a_member_add_that_lost_to_another_writer_runs_again() -> None:
    tx = AsyncMock()
    tx.get_user_by_email = AsyncMock(side_effect=[RuntimeError(CONFLICT), None])
    proc = _processor(tx)

    # The second attempt runs, and finds no such user.
    assert await proc.on_user_group_member_added("g", "u@x.com", PermissionType.READ, "conn-1") is False
    assert tx.get_user_by_email.await_count == 2


async def test_a_membership_that_lost_to_another_writer_runs_again() -> None:
    tx = AsyncMock()
    tx.create_user_group_membership = AsyncMock(side_effect=[RuntimeError(CONFLICT), True])
    proc = _processor(tx)

    assert await proc.create_user_group_membership("src-u", "g", "conn-1") is True
    assert tx.create_user_group_membership.await_count == 2


async def test_the_transaction_store_passes_on_a_conflict_that_aborted_its_transaction() -> None:
    provider = MagicMock()
    provider.is_transient_error = MagicMock(side_effect=lambda e, **_: is_write_conflict(e))
    store = GraphTransactionStore(provider, "txn-1")
    store.get_user_by_source_id = AsyncMock(side_effect=RuntimeError(CONFLICT))

    with pytest.raises(RuntimeError, match=r"\[1200\]"):
        await store.create_user_group_membership("src-u", "g", "conn-1")

    store.get_user_by_source_id = AsyncMock(side_effect=RuntimeError("bad query"))
    assert await store.create_user_group_membership("src-u", "g", "conn-1") is False


@pytest.mark.parametrize("error, lost", [
    (ArangoQueryError("Query failed", 409, '{"errorNum": 1200, "errorMessage": "write-write conflict"}'), True),
    (RuntimeError("bad query"), False),
])
async def test_a_kb_retag_that_lost_to_another_writer_is_decided_again(error, lost) -> None:
    """False sends the push back to read the record and decide again."""
    tx = MagicMock()
    tx.retag_push_record = AsyncMock(side_effect=error)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=tx)
    ctx.__aexit__ = AsyncMock(return_value=False)
    processor = SimpleNamespace(data_store_provider=SimpleNamespace(transaction=MagicMock(return_value=ctx)))
    service = KbPushService.__new__(KbPushService)
    service._processor = AsyncMock(return_value=processor)
    existing = {"id": "r1", "externalRevisionId": "rev-old"}
    v = SimpleNamespace(digest="d", content_rev=1)

    with patch("app.connectors.sources.localKB.push.service.C.revision_id", return_value="rev-new"):
        if lost:
            assert await service._retag(existing, v, "kb-1", "org-1") is False
        else:
            with pytest.raises(RuntimeError):
                await service._retag(existing, v, "kb-1", "org-1")
