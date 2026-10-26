"""An Arango KB delete that loses to a concurrent push is run again (T42u), and measured by what remains (T42g)."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.common.cascade_retry import CASCADE_ATTEMPTS

CONFLICT = Exception('{"errorNum": 1200, "errorMessage": "conflict: write-write conflict"}')


@pytest.fixture
def p() -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(MagicMock(spec=logging.Logger), AsyncMock())
    provider.http_client = AsyncMock()
    provider.get_document = AsyncMock(return_value={"_key": "c1"})
    provider._collect_connector_entities = AsyncMock(return_value={
        "record_keys": ["r1"], "record_group_keys": [], "role_keys": [], "group_keys": [], "virtual_record_ids": [],
    })
    provider._get_all_edge_collections = AsyncMock(return_value=[])
    provider._collect_isoftype_targets = AsyncMock(return_value=([], True))
    provider._collect_connector_taxonomy_targets = AsyncMock(return_value=([], True))
    provider._delete_edges_by_connector_id = AsyncMock(return_value=(0, []))
    provider.remove_authenticated_as = AsyncMock()
    provider.delete_blocks_by_connector_id = AsyncMock(return_value=0)
    provider.begin_transaction = AsyncMock(side_effect=lambda **_: f"txn{provider.begin_transaction.await_count}")
    provider.commit_transaction = AsyncMock()
    provider.rollback_transaction = AsyncMock()
    provider._delete_type_docs_matching_record_keys = AsyncMock(return_value=0)
    provider._delete_nodes_by_connector_id = AsyncMock(return_value=(0, True))
    provider._delete_stage_states_by_connector_id = AsyncMock(return_value=(0, True))
    provider._sweep_orphan_type_docs_for_org = AsyncMock(return_value=0)
    provider._delete_unlinked_taxonomy_nodes = AsyncMock(return_value=0)
    provider._delete_nodes_by_keys = AsyncMock(return_value=(1, 0))
    provider._delete_isoftype_targets_from_collected = AsyncMock(return_value=(0, []))
    provider.get_existing_keys = AsyncMock(return_value=set())
    provider.http_client.execute_aql = AsyncMock(return_value=[])
    return provider


@pytest.mark.asyncio
async def test_a_write_conflict_runs_the_transaction_again_in_a_fresh_one(p) -> None:
    p._delete_nodes_by_keys = AsyncMock(side_effect=[CONFLICT, (1, 0), (0, 0), (0, 0), (0, 0), (1, 0)])

    result = await p.delete_connector_instance("c1", "org1")

    assert result["success"] is True, result
    assert result["cascade_retries"] == 1
    assert p.begin_transaction.await_count == 2
    p.rollback_transaction.assert_awaited_once_with("txn1")
    p.commit_transaction.assert_awaited_once_with("txn2")


@pytest.mark.asyncio
async def test_without_the_retry_one_conflict_fails_the_delete(p, monkeypatch) -> None:
    """Negative control: the round-3 code. The delete fails and the KB stays DELETING with its record."""
    monkeypatch.setattr("app.services.graph_db.arango.arango_http_provider.CASCADE_ATTEMPTS", 1)
    p._delete_nodes_by_keys = AsyncMock(side_effect=[CONFLICT, (1, 0)])

    result = await p.delete_connector_instance("c1", "org1")

    assert result["success"] is False and "1200" in result["error"]
    assert p.begin_transaction.await_count == 1


@pytest.mark.asyncio
async def test_the_retry_is_bounded(p) -> None:
    p._delete_nodes_by_keys = AsyncMock(side_effect=CONFLICT)

    result = await p.delete_connector_instance("c1", "org1")

    assert result["success"] is False
    assert p.begin_transaction.await_count == CASCADE_ATTEMPTS
    assert p.rollback_transaction.await_count == CASCADE_ATTEMPTS


@pytest.mark.asyncio
async def test_another_error_is_not_retried(p) -> None:
    p._delete_nodes_by_keys = AsyncMock(side_effect=RuntimeError("syntax error"))

    result = await p.delete_connector_instance("c1", "org1")

    assert result["success"] is False and p.begin_transaction.await_count == 1


@pytest.mark.asyncio
async def test_a_caller_owned_transaction_is_never_retried(p) -> None:
    p._delete_nodes_by_keys = AsyncMock(side_effect=CONFLICT)

    result = await p.delete_connector_instance("c1", "org1", transaction="theirs")

    assert result["success"] is False
    p.begin_transaction.assert_not_awaited()
    p.rollback_transaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_second_deleter_that_found_the_records_gone_succeeds(p) -> None:
    """The rival removed them first: 0 deleted here, none remain, so this delete is complete."""
    p._delete_nodes_by_keys = AsyncMock(return_value=(0, 0))

    result = await p.delete_connector_instance("c1", "org1")

    assert result["success"] is True, result
    assert {c.args[0] for c in p.get_existing_keys.await_args_list} >= {"records", "apps"}


@pytest.mark.asyncio
@pytest.mark.parametrize("collection", ["records", "recordGroups", "roles", "groups", "apps"])
async def test_a_node_that_remains_in_any_collection_fails_and_rolls_back(p, collection) -> None:
    p._collect_connector_entities.return_value.update(
        record_keys=["r1"], record_group_keys=["g1"], role_keys=["ro1"], group_keys=["gr1"],
    )
    p.get_existing_keys = AsyncMock(side_effect=lambda coll, keys, tx=None: {"x"} if coll == collection else set())

    result = await p.delete_connector_instance("c1", "org1")

    assert result["success"] is False and "remain" in result["error"]
    p.rollback_transaction.assert_awaited()
    p.commit_transaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_existing_keys_reads_the_collection_and_raises_on_a_failed_read() -> None:
    q = ArangoHTTPProvider(MagicMock(spec=logging.Logger), AsyncMock())
    q.http_client = AsyncMock()
    q.http_client.execute_aql = AsyncMock(return_value=["a"])
    assert await q.get_existing_keys("files", ["a", "b", "a", ""], "txn") == {"a"}
    assert q.http_client.execute_aql.await_args.args[1] == {"@collection": "files", "keys": ["a", "b"]}
    q.http_client.execute_aql.side_effect = RuntimeError("down")
    with pytest.raises(RuntimeError):
        await q.get_existing_keys("files", ["a"])
    assert await q.get_existing_keys("files", []) == set()
