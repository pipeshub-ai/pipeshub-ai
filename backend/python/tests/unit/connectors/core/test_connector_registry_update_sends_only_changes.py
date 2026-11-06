"""update_connector_instance writes only the changed keys, not the whole stored app document.

Replaying the stored document sent every legacy key it held, and a key the schema no longer
accepts rejected each later toggle, rename or config save.
"""

from unittest.mock import AsyncMock, MagicMock

from app.connectors.core.registry.connector_registry import ConnectorRegistry


async def test_only_the_updated_keys_and_the_timestamp_reach_update_node() -> None:
    container = MagicMock()
    registry = ConnectorRegistry(container)
    graph = AsyncMock()
    stored = {
        "_key": "c1", "_id": "apps/c1", "id": "c1", "orgId": "org-1", "type": "Gmail", "name": "Old",
        "scope": "personal", "createdBy": "user-1", "legacyField": "from an older release",
    }
    graph.get_document.return_value = dict(stored)
    container.data_store = AsyncMock(return_value=MagicMock(graph_provider=graph))

    result = await registry.update_connector_instance("c1", {"isActive": True}, "user-1", "org-1", is_admin=False)

    (connector_id, _collection, node_updates), _ = graph.update_node.await_args
    assert connector_id == "c1"
    assert set(node_updates) == {"isActive", "updatedAtTimestamp"}
    # The caller still gets the whole merged document back.
    assert result is not None and result["legacyField"] == "from an older release" and result["isActive"] is True
