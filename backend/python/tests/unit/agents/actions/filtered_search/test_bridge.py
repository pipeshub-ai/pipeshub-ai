"""Unit tests for `FilteredRetrievalBridge`: batch resolution + permission
gating (the security-critical path — native results are only ever a
candidate universe until each one passes `get_knowledge_hub_node_access`),
and content-search scoping via `search_within_virtual_record_ids`."""

from unittest.mock import AsyncMock, MagicMock

from app.agents.actions.filtered_search.bridge import (
    FilteredRetrievalBridge,
    search_within_virtual_record_ids,
)
from app.agents.actions.filtered_search.models import FilteredRecord, FilteredSearchUniverse
from app.config.constants.arangodb import Connectors, OriginTypes
from app.models.entities import Record, RecordType


def _make_record(external_record_id: str, record_id: str | None = None, virtual_record_id: str | None = None) -> Record:
    return Record(
        id=record_id or f"internal-{external_record_id}",
        record_name=f"Record {external_record_id}",
        record_type=RecordType.TICKET,
        external_record_id=external_record_id,
        version=1,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.JIRA,
        connector_id="conn-1",
        virtual_record_id=virtual_record_id or f"vr-{external_record_id}",
    )


def _universe(*external_ids: str) -> FilteredSearchUniverse:
    return FilteredSearchUniverse(
        connector_type="JIRA",
        records=[FilteredRecord(external_id=eid, name=f"name-{eid}") for eid in external_ids],
        native_query="project = CORE",
    )


class TestResolveAndGate:
    async def test_empty_universe_short_circuits(self) -> None:
        graph = MagicMock()
        bridge = FilteredRetrievalBridge(graph, retrieval_service=None, folder_mime_types=[])
        result = await bridge.resolve_and_gate(_universe(), "conn-1", "user-key", "org-1")
        assert result.accessible_records == []
        graph.get_records_by_external_ids.assert_not_called()

    async def test_accessible_records_pass_and_denied_are_dropped(self) -> None:
        graph = MagicMock()
        r1, r2 = _make_record("E1"), _make_record("E2")
        graph.get_records_by_external_ids = AsyncMock(return_value=[r1, r2])

        async def _access(node_id: str, **kwargs):
            return {"id": node_id} if node_id == r1.id else None

        graph.get_knowledge_hub_node_access = AsyncMock(side_effect=_access)

        bridge = FilteredRetrievalBridge(graph, retrieval_service=None, folder_mime_types=[])
        result = await bridge.resolve_and_gate(_universe("E1", "E2"), "conn-1", "user-key", "org-1")

        assert [r.id for r in result.accessible_records] == [r1.id]
        assert result.denied_count == 1
        assert result.unresolved_external_ids == []

    async def test_unresolved_external_ids_reported_separately_from_denied(self) -> None:
        """An external ID the graph has never heard of (not yet synced, or
        from a different connector) must not be conflated with a
        permission denial — different remediation for each."""
        graph = MagicMock()
        r1 = _make_record("E1")
        graph.get_records_by_external_ids = AsyncMock(return_value=[r1])
        graph.get_knowledge_hub_node_access = AsyncMock(return_value={"id": r1.id})

        bridge = FilteredRetrievalBridge(graph, retrieval_service=None, folder_mime_types=[])
        result = await bridge.resolve_and_gate(_universe("E1", "E2-missing"), "conn-1", "user-key", "org-1")

        assert len(result.accessible_records) == 1
        assert result.unresolved_external_ids == ["E2-missing"]

    async def test_join_uses_external_record_id_not_short_name(self) -> None:
        """Regression guard for the documented "query keys vs join keys"
        caveat: the bridge must call `get_records_by_external_ids` with the
        raw external ids from the universe, never a short_name/display
        value, and never re-derive matching by name."""
        graph = MagicMock()
        graph.get_records_by_external_ids = AsyncMock(return_value=[])
        bridge = FilteredRetrievalBridge(graph, retrieval_service=None, folder_mime_types=["application/vnd.folder"])
        await bridge.resolve_and_gate(_universe("10001", "10002"), "conn-1", "user-key", "org-1")
        graph.get_records_by_external_ids.assert_awaited_once_with(
            connector_id="conn-1", external_ids=["10001", "10002"],
        )


class TestSearchContentWithin:
    async def test_no_op_when_no_accessible_records(self) -> None:
        from app.agents.actions.filtered_search.bridge import BridgedResult

        retrieval = MagicMock()
        retrieval.search_with_filters = AsyncMock()
        graph = MagicMock()
        bridge = FilteredRetrievalBridge(graph, retrieval_service=retrieval, folder_mime_types=[])

        bridged = BridgedResult(accessible_records=[])
        result = await bridge.search_content_within(bridged, "token refresh", "user-1", "org-1")

        assert result.content_search_ran is False
        retrieval.search_with_filters.assert_not_called()

    async def test_scopes_retrieval_to_gated_virtual_record_ids_only(self) -> None:
        """The security-critical assertion: only the (already
        permission-gated) virtual_record_ids reach `search_with_filters` —
        this is what prevents the known `virtual_record_ids_from_tool`
        bypass from leaking un-gated content."""
        from app.agents.actions.filtered_search.bridge import BridgedResult

        retrieval = MagicMock()
        retrieval.search_with_filters = AsyncMock(return_value={"searchResults": [{"id": "x"}]})
        graph = MagicMock()
        bridge = FilteredRetrievalBridge(graph, retrieval_service=retrieval, folder_mime_types=[])

        accessible = [_make_record("E1", virtual_record_id="vr-1"), _make_record("E2", virtual_record_id="vr-2")]
        bridged = BridgedResult(accessible_records=accessible)
        result = await bridge.search_content_within(bridged, "token refresh", "user-1", "org-1", limit=10)

        assert result.content_search_ran is True
        assert result.retrieval_response == {"searchResults": [{"id": "x"}]}
        retrieval.search_with_filters.assert_awaited_once_with(
            queries=["token refresh"], user_id="user-1", org_id="org-1", limit=10,
            virtual_record_ids_from_tool=["vr-1", "vr-2"],
        )


class TestSearchWithinVirtualRecordIds:
    async def test_returns_none_when_no_ids(self) -> None:
        retrieval = MagicMock()
        retrieval.search_with_filters = AsyncMock()
        result = await search_within_virtual_record_ids(retrieval, [], "q", "u1", "o1")
        assert result is None
        retrieval.search_with_filters.assert_not_called()

    async def test_returns_none_when_no_retrieval_service(self) -> None:
        result = await search_within_virtual_record_ids(None, ["vr-1"], "q", "u1", "o1")
        assert result is None

    async def test_returns_none_and_swallows_exception_on_failure(self) -> None:
        retrieval = MagicMock()
        retrieval.search_with_filters = AsyncMock(side_effect=RuntimeError("timeout"))
        result = await search_within_virtual_record_ids(retrieval, ["vr-1"], "q", "u1", "o1")
        assert result is None

    async def test_success_returns_raw_retrieval_response(self) -> None:
        retrieval = MagicMock()
        retrieval.search_with_filters = AsyncMock(return_value={"searchResults": []})
        result = await search_within_virtual_record_ids(retrieval, ["vr-1"], "q", "u1", "o1")
        assert result == {"searchResults": []}
