"""Every writer touches shared target nodes in one order, whatever their names."""

import logging
from unittest.mock import AsyncMock

from app.modules.transformers.edge_reconciler import EdgeReconciler


async def test_edges_are_created_and_deleted_in_target_order_not_name_order():
    store = AsyncMock()
    store.get_edges_from_node_with_target_name = AsyncMock(return_value=[
        {"_to": "namedEntities/k9", "name": "aaa"},
        {"_to": "namedEntities/k8", "name": "zzz"},
    ])
    new_tos = {"namedEntities/k3": "Acme", "namedEntities/k1": "Zeta", "namedEntities/k2": "ACME"}

    await EdgeReconciler(logging.getLogger("test")).reconcile(
        store, record_id="r", record_from="records/r", edge_collection="mentionsEntity", new_tos=new_tos, label="t",
    )

    (created, _), _ = store.batch_create_edges.await_args
    assert [edge["to_id"] for edge in created] == ["k1", "k2", "k3"]
    (deleted, _), _ = store.batch_delete_edges.await_args
    assert [edge["to_id"] for edge in deleted] == ["k8", "k9"]
