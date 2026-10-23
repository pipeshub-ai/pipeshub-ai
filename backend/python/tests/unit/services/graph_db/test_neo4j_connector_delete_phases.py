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
