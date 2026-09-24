"""AccessibleRecordsInvalidator: resolves the org, guards the KB-only policy, and
never lets a cache problem fail the caller's real work."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.services.cache.accessible_records_cache import AccessibleRecordsInvalidator

ORG = "org-1"


def _make(app_doc=None, get_document_error=None, org_edges=None):
    cache = MagicMock()
    cache.invalidate_connector = AsyncMock()
    cache.invalidate_kb = AsyncMock()

    graph = MagicMock()
    if get_document_error is not None:
        graph.get_document = AsyncMock(side_effect=get_document_error)
    else:
        graph.get_document = AsyncMock(return_value=app_doc)
    graph.get_edges_to_node = AsyncMock(return_value=org_edges or [])

    return AccessibleRecordsInvalidator(MagicMock(), cache, graph), cache, graph


class TestConnectorSyncCompleted:
    async def test_resolves_org_from_the_app_doc(self) -> None:
        inv, cache, graph = _make(app_doc={"orgId": ORG, "type": "S3"})

        await inv.on_connector_sync_completed("conn-1")

        graph.get_document.assert_awaited_once_with("conn-1", CollectionNames.APPS.value)
        graph.get_edges_to_node.assert_not_called()
        cache.invalidate_connector.assert_awaited_once_with(ORG, "conn-1")

    async def test_resolves_org_from_org_app_relation_when_property_missing(self) -> None:
        inv, cache, graph = _make(
            app_doc={"type": "S3"},
            org_edges=[{"from_id": ORG}],
        )

        await inv.on_connector_sync_completed("conn-1")

        graph.get_edges_to_node.assert_awaited_once_with(
            f"{CollectionNames.APPS.value}/conn-1",
            CollectionNames.ORG_APP_RELATION.value,
        )
        cache.invalidate_connector.assert_awaited_once_with(ORG, "conn-1")

    async def test_resolves_org_from_arango_edge_shape(self) -> None:
        inv, cache, _ = _make(
            app_doc={"type": "S3"},
            org_edges=[{"_from": f"organizations/{ORG}"}],
        )

        await inv.on_connector_sync_completed("conn-1")

        cache.invalidate_connector.assert_awaited_once_with(ORG, "conn-1")

    async def test_supplied_org_skips_the_lookup(self) -> None:
        inv, cache, graph = _make()

        await inv.on_connector_sync_completed("conn-1", org_id=ORG)

        graph.get_document.assert_not_called()
        cache.invalidate_connector.assert_awaited_once_with(ORG, "conn-1")

    async def test_unknown_app_is_a_noop(self) -> None:
        inv, cache, _ = _make(app_doc=None)
        await inv.on_connector_sync_completed("conn-1")
        cache.invalidate_connector.assert_not_called()

    async def test_blank_connector_id_is_a_noop(self) -> None:
        inv, cache, graph = _make()
        await inv.on_connector_sync_completed("")
        graph.get_document.assert_not_called()
        cache.invalidate_connector.assert_not_called()

    async def test_graph_failure_is_swallowed(self) -> None:
        inv, cache, _ = _make(get_document_error=RuntimeError("graph down"))
        await inv.on_connector_sync_completed("conn-1")
        cache.invalidate_connector.assert_not_called()

    async def test_cache_failure_is_swallowed(self) -> None:
        inv, cache, _ = _make(app_doc={"orgId": ORG})
        cache.invalidate_connector = AsyncMock(side_effect=RuntimeError("redis down"))
        await inv.on_connector_sync_completed("conn-1")  # must not raise


class TestKbRecordsChanged:
    async def test_invalidates_a_kb(self) -> None:
        inv, cache, _ = _make(app_doc={"orgId": ORG, "type": Connectors.KNOWLEDGE_BASE.value})

        await inv.on_kb_records_changed("kb-1")

        cache.invalidate_kb.assert_awaited_once_with(ORG, "kb-1")

    async def test_non_kb_app_is_a_noop(self) -> None:
        """The cascade-delete hook also fires for ordinary connectors."""
        inv, cache, _ = _make(app_doc={"orgId": ORG, "type": "DRIVE"})

        await inv.on_kb_records_changed("conn-1")

        cache.invalidate_kb.assert_not_called()

    async def test_missing_app_doc_is_a_noop(self) -> None:
        inv, cache, _ = _make(app_doc=None)
        await inv.on_kb_records_changed("kb-1")
        cache.invalidate_kb.assert_not_called()

    async def test_app_without_org_is_a_noop(self) -> None:
        inv, cache, _ = _make(app_doc={"type": Connectors.KNOWLEDGE_BASE.value})
        await inv.on_kb_records_changed("kb-1")
        cache.invalidate_kb.assert_not_called()

    async def test_supplied_org_still_checks_the_type(self) -> None:
        inv, cache, _ = _make(app_doc={"orgId": ORG, "type": "SLACK"})
        await inv.on_kb_records_changed("conn-1", org_id=ORG)
        cache.invalidate_kb.assert_not_called()

    async def test_graph_failure_is_swallowed(self) -> None:
        inv, cache, _ = _make(get_document_error=RuntimeError("graph down"))
        await inv.on_kb_records_changed("kb-1")
        cache.invalidate_kb.assert_not_called()


class TestRecordIndexed:
    async def test_kb_record_invalidates(self) -> None:
        inv, cache, _ = _make(app_doc={"orgId": ORG, "type": Connectors.KNOWLEDGE_BASE.value})

        await inv.on_record_indexed(
            connector_name=Connectors.KNOWLEDGE_BASE, connector_id="kb-1", org_id=ORG
        )

        cache.invalidate_kb.assert_awaited_once_with(ORG, "kb-1")

    async def test_accepts_the_enum_or_its_value(self) -> None:
        inv, cache, _ = _make()
        await inv.on_record_indexed(connector_name="KB", connector_id="kb-1", org_id=ORG)
        cache.invalidate_kb.assert_awaited_once_with(ORG, "kb-1")

    async def test_connector_records_are_ignored(self) -> None:
        """A full sync flips thousands of records COMPLETED; invalidating per
        record would empty the cache exactly when the graph is busiest."""
        inv, cache, graph = _make()

        await inv.on_record_indexed(connector_name="DRIVE", connector_id="conn-1", org_id=ORG)

        cache.invalidate_kb.assert_not_called()
        cache.invalidate_connector.assert_not_called()
        graph.get_document.assert_not_called()

    async def test_falls_back_to_the_record_group_id(self) -> None:
        inv, cache, _ = _make()
        await inv.on_record_indexed(
            connector_name="KB", connector_id=None, external_record_group_id="kb-9", org_id=ORG
        )
        cache.invalidate_kb.assert_awaited_once_with(ORG, "kb-9")

    async def test_resolves_org_when_missing(self) -> None:
        inv, cache, graph = _make(app_doc={"orgId": ORG})
        await inv.on_record_indexed(connector_name="KB", connector_id="kb-1")
        graph.get_document.assert_awaited_once_with("kb-1", CollectionNames.APPS.value)
        cache.invalidate_kb.assert_awaited_once_with(ORG, "kb-1")

    async def test_no_kb_id_is_a_noop(self) -> None:
        inv, cache, _ = _make()
        await inv.on_record_indexed(connector_name="KB", connector_id=None)
        cache.invalidate_kb.assert_not_called()

    async def test_missing_connector_name_is_a_noop(self) -> None:
        inv, cache, _ = _make()
        await inv.on_record_indexed(connector_id="kb-1", org_id=ORG)
        cache.invalidate_kb.assert_not_called()

    async def test_failures_are_swallowed(self) -> None:
        inv, cache, _ = _make(app_doc={"orgId": ORG})
        cache.invalidate_kb = AsyncMock(side_effect=RuntimeError("redis down"))
        await inv.on_record_indexed(connector_name="KB", connector_id="kb-1", org_id=ORG)
