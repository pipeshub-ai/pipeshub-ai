"""A connector delete commits per batch: one explicit transaction would hit the server timeout."""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


def _provider() -> Neo4jProvider:
    p = Neo4jProvider.__new__(Neo4jProvider)
    p.logger = MagicMock()
    p.client = MagicMock(execute_query=AsyncMock(return_value=[]))
    # Every other coroutine the delete may call (cleanup steps added later) is a mock too.
    for name, _ in inspect.getmembers(Neo4jProvider, inspect.iscoroutinefunction):
        if name != "_delete_connector_instance":
            setattr(p, name, AsyncMock())
    p.get_document = AsyncMock(return_value={"type": "Jira"})
    p._collect_connector_entities = AsyncMock(return_value={
        "record_keys": [], "record_group_keys": [], "role_keys": [], "group_keys": [],
        "virtual_record_ids": [],
    })
    p._get_all_edge_collections = AsyncMock(return_value=[])
    p._collect_isoftype_targets = AsyncMock(return_value=([], True))
    p.delete_blocks_by_connector_id = AsyncMock(return_value=0)
    p.begin_transaction = AsyncMock(return_value="txn")
    p.commit_transaction = AsyncMock()
    p.rollback_transaction = AsyncMock()
    p._delete_isoftype_targets_from_collected = AsyncMock(return_value=(0, True))
    p._collect_connector_taxonomy_targets = AsyncMock(return_value=([], True))
    p._delete_type_docs_matching_record_keys = AsyncMock(return_value=0)
    p._sweep_orphan_type_docs_for_org = AsyncMock(return_value=0)
    p._delete_unlinked_taxonomy_nodes = AsyncMock(return_value=0)
    p._delete_nodes_by_keys.side_effect = lambda *a, **k: (1, 0)
    p._delete_nodes_by_connector_id = AsyncMock(return_value=(0, True))
    p._delete_stage_states_by_connector_id = AsyncMock(return_value=(0, True))
    p.remove_authenticated_as = AsyncMock()
    p._stash_connector_delete_handles = AsyncMock(side_effect=lambda _c, groups, vrids: (list(groups), vrids))
    p._delete_connector_with_its_delete_handles = AsyncMock(return_value=1)
    return p


@pytest.mark.asyncio
async def test_connector_delete_does_not_open_an_explicit_transaction() -> None:
    p = _provider()
    result = await p._delete_connector_instance("c1", "org")

    assert result["success"] is True, result
    assert p.begin_transaction.await_args.kwargs["explicit"] is False


@pytest.mark.asyncio
async def test_failed_batch_keeps_the_app_node() -> None:
    p = _provider()
    p._collect_connector_entities.return_value["role_keys"] = ["r1"]
    p._delete_nodes_by_keys.side_effect = lambda _tx, keys, coll: (0, 1) if keys == ["r1"] else (1, 0)

    result = await p._delete_connector_instance("c1", "org")

    assert result["success"] is False, result
    assert all(c.args[1] != ["c1"] for c in p._delete_nodes_by_keys.await_args_list)
    p._delete_connector_with_its_delete_handles.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_retry_reports_the_ids_an_earlier_attempt_already_deleted() -> None:
    """An attempt that failed after its record-group batches committed leaves fewer groups to
    collect; the stash still holds them, so the vector cleanup strips every one."""
    p = _provider()
    p._collect_connector_entities.return_value.update(record_group_keys=["g2"], virtual_record_ids=["v2"])
    p._stash_connector_delete_handles.side_effect = None
    p._stash_connector_delete_handles.return_value = (["g1", "g2"], ["v1", "v2"])

    result = await p._delete_connector_instance("c1", "org")

    assert result["success"] is True, result
    assert result["record_group_ids"] == ["g1", "g2"]
    # Without membership cleanup the explicit list is the only way to reach the points.
    assert result["virtual_record_ids"] == ["v1", "v2"]
    p._stash_connector_delete_handles.assert_awaited_once_with("c1", ["g2"], ["v2"])
    p._delete_connector_with_its_delete_handles.assert_awaited_once_with("c1")


@pytest.mark.asyncio
async def test_nothing_is_deleted_when_the_ids_cannot_be_stashed() -> None:
    p = _provider()
    p._stash_connector_delete_handles.side_effect = ConnectionError("graph down")

    result = await p._delete_connector_instance("c1", "org")

    assert result["success"] is False, result
    p.delete_blocks_by_connector_id.assert_not_awaited()
    p._delete_nodes_by_keys.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_caller_transaction_deletes_without_a_stash() -> None:
    """One transaction commits or rolls back whole: nothing is left for a retry to miss."""
    p = _provider()

    result = await p._delete_connector_instance("c1", "org", transaction="caller-txn")

    assert result["success"] is True, result
    p._stash_connector_delete_handles.assert_not_awaited()
    p._delete_connector_with_its_delete_handles.assert_not_awaited()
    assert any(c.args[1] == ["c1"] for c in p._delete_nodes_by_keys.await_args_list)


class TestStash:
    @staticmethod
    def _stash_provider(existing: list[dict]) -> Neo4jProvider:
        p = Neo4jProvider.__new__(Neo4jProvider)
        p.logger = MagicMock()
        p.client = MagicMock(execute_query=AsyncMock(side_effect=lambda q, **_: existing if "RETURN" in q else []))
        return p

    @pytest.mark.asyncio
    async def test_only_new_ids_are_written_and_all_are_returned(self) -> None:
        p = self._stash_provider([{"kind": "recordGroup", "ids": ["g1"]}, {"kind": "virtualRecord", "ids": ["v1"]}])

        groups, vrids = await p._stash_connector_delete_handles("c1", ["g1", "g2", "g2", ""], ["v2"])

        assert (groups, vrids) == (["g1", "g2"], ["v1", "v2"])
        writes = [c.kwargs["parameters"] for c in p.client.execute_query.await_args_list[1:]]
        assert writes == [
            {"connector_id": "c1", "kind": "recordGroup", "ids": ["g2"]},
            {"connector_id": "c1", "kind": "virtualRecord", "ids": ["v2"]},
        ]

    @pytest.mark.asyncio
    async def test_ids_are_written_in_chunks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import app.services.graph_db.neo4j.neo4j_provider as module

        monkeypatch.setattr(module, "_CONNECTOR_DELETE_HANDLE_CHUNK", 2)
        p = self._stash_provider([])

        groups, vrids = await p._stash_connector_delete_handles("c1", ["a", "b", "c"], None)

        assert (groups, vrids) == (["a", "b", "c"], None)
        assert [c.kwargs["parameters"]["ids"] for c in p.client.execute_query.await_args_list[1:]] == [["a", "b"], ["c"]]

    @pytest.mark.asyncio
    async def test_a_failed_write_raises(self) -> None:
        p = self._stash_provider([])
        p.client.execute_query.side_effect = [[], ConnectionError("graph down")]
        with pytest.raises(ConnectionError):
            await p._stash_connector_delete_handles("c1", ["g1"], None)
