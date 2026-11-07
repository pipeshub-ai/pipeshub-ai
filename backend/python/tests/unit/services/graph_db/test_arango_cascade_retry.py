"""An Arango KB delete that loses to a concurrent push is run again, and measured by what remains."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.common.cascade_retry import CASCADE_ATTEMPTS

CONFLICT = Exception('{"errorNum": 1200, "errorMessage": "conflict: write-write conflict"}')


@pytest.fixture(autouse=True)
def backoff(monkeypatch) -> AsyncMock:
    wait = AsyncMock()
    monkeypatch.setattr("app.services.graph_db.arango.arango_http_provider.cascade_backoff", wait)
    return wait


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
    provider._count_connector_blocks_and_stage_states = AsyncMock(return_value=0)
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


@pytest.mark.asyncio
async def test_a_conflict_deleting_stage_states_runs_the_transaction_again(p, backoff) -> None:
    """Indexing still writing the connector's stage states: the round is run again, after a
    longer wait than a push conflict needs, instead of failing the delete outright."""
    p._delete_stage_states_by_connector_id = AsyncMock(side_effect=[CONFLICT, (3, True), (0, True)])

    result = await p.delete_connector_instance("c1", "org1")

    assert result["success"] is True, result
    assert result["cascade_retries"] == 1
    assert backoff.await_args.kwargs["base"] >= 0.5


@pytest.mark.asyncio
async def test_the_stage_state_delete_raises_a_conflict_and_reports_other_failures() -> None:
    provider = ArangoHTTPProvider(MagicMock(spec=logging.Logger), AsyncMock())
    provider.http_client = AsyncMock()

    provider.http_client.execute_aql = AsyncMock(side_effect=CONFLICT)
    with pytest.raises(Exception, match="1200"):
        await provider._delete_stage_states_by_connector_id("txn1", "c1")

    provider.http_client.execute_aql = AsyncMock(side_effect=RuntimeError("disk full"))
    assert await provider._delete_stage_states_by_connector_id("txn1", "c1") == (0, False)




@pytest.mark.asyncio
async def test_a_sweep_that_loses_to_a_writer_runs_again_until_none_is_left(p, backoff) -> None:
    """Outside the delete transaction a block REMOVE ignores per-document errors and a stage-state
    conflict is raised, which fails that pass: either way rows stay, so the count, not the calls, decides."""
    # The first of each is the cascade's own.
    p.delete_blocks_by_connector_id = AsyncMock(side_effect=[0, 0, 0, 6])
    p._delete_stage_states_by_connector_id = AsyncMock(side_effect=[(0, True), CONFLICT, (0, True), (1, True)])
    p._count_connector_blocks_and_stage_states = AsyncMock(side_effect=[7, 0])

    result = await p.delete_connector_instance("c1", "org1")

    assert result["success"] is True, result
    assert result["cleanupPending"] is False
    assert [c.args for c in p.delete_blocks_by_connector_id.await_args_list] == [("c1",)] * 4
    assert [c.args for c in p._delete_stage_states_by_connector_id.await_args_list[1:]] == [(None, "c1")] * 3
    assert p._count_connector_blocks_and_stage_states.await_count == 2


@pytest.mark.asyncio
async def test_one_late_write_pass_removes_blocks_and_stage_states_then_counts(p) -> None:
    p.delete_blocks_by_connector_id = AsyncMock(return_value=4)
    p._delete_stage_states_by_connector_id = AsyncMock(return_value=(2, True))
    p._count_connector_blocks_and_stage_states = AsyncMock(return_value=1)

    assert await p.sweep_deleted_connector_writes("c1") == (6, 1)
    p._delete_stage_states_by_connector_id.assert_awaited_once_with(None, "c1")


@pytest.mark.asyncio
async def test_a_late_write_pass_raises_what_the_store_raises(p) -> None:
    """The late-write sweep retries a pass that failed; it must not read as an empty connector."""
    p._delete_stage_states_by_connector_id = AsyncMock(side_effect=CONFLICT)
    with pytest.raises(Exception, match="1200"):
        await p.sweep_deleted_connector_writes("c1")


@pytest.mark.asyncio
async def test_a_block_that_conflicts_does_not_hold_back_the_stage_states(p) -> None:
    """Both removals run in every pass; the pass still fails, so the next one retries the blocks."""
    p.delete_blocks_by_connector_id = AsyncMock(side_effect=CONFLICT)
    p._delete_stage_states_by_connector_id = AsyncMock(return_value=(2, True))
    p._count_connector_blocks_and_stage_states = AsyncMock(return_value=0)

    with pytest.raises(Exception, match="1200"):
        await p.sweep_deleted_connector_writes("c1")
    p._delete_stage_states_by_connector_id.assert_awaited_once_with(None, "c1")
    p._count_connector_blocks_and_stage_states.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_pass_raises_the_block_error_when_both_removals_fail(p) -> None:
    p.delete_blocks_by_connector_id = AsyncMock(side_effect=RuntimeError("blocks"))
    p._delete_stage_states_by_connector_id = AsyncMock(side_effect=CONFLICT)

    with pytest.raises(RuntimeError, match="blocks"):
        await p.sweep_deleted_connector_writes("c1")
    p._delete_stage_states_by_connector_id.assert_awaited_once_with(None, "c1")


@pytest.mark.asyncio
async def test_a_late_write_pass_fails_on_an_edge_it_cannot_remove(p) -> None:
    """On its own, an edge REMOVE skips a conflicting edge while its block still goes; nothing
    counts edges, so the pass must fail and run again rather than leave the edge pointing at nothing."""
    queries: list[str] = []

    async def aql(query, bind_vars=None, txn_id=None) -> list:
        queries.append(query)
        return ["b1"] if "LIMIT @batch" in query else [1]

    del p.delete_blocks_by_connector_id  # the provider's own, not the fixture's mock
    p.http_client.execute_aql = AsyncMock(side_effect=aql)

    await p.sweep_deleted_connector_writes("c1")
    swept_edges = next(q for q in queries if "edge_keys" in q)
    queries.clear()
    await p.delete_blocks_by_connector_id("c1")
    other_edges = next(q for q in queries if "edge_keys" in q)

    assert "ignoreErrors" not in swept_edges
    assert "ignoreErrors: true" in other_edges  # every other standalone caller keeps its behaviour
