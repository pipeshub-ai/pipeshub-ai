"""Unit tests for `filtered_search.execution.run_filtered_search`: adapter
dispatch, filter-only validation, and permission-gated response shape.

`build_filter_spec`/`FilterSpec` are gone — validation and identity
substitution now happen per-adapter against a raw native query string, see
`test_adapter_*.py` for those cases."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.actions.filtered_search import execution as execution_module
from app.agents.actions.filtered_search.execution import run_filtered_search
from app.agents.actions.filtered_search.models import (
    FilterCapabilityDescriptor,
    FilteredSearchUniverse,
    GroupReference,
)
from app.agents.actions.filtered_search.registry import FilterAdapterRegistry
from app.models.entities import RecordGroupType

pytestmark = pytest.mark.asyncio


class _FakeAdapter:
    _caps = FilterCapabilityDescriptor(
        connector_type="FAKE",
        record_group_noun="project",
        container_group_types=[RecordGroupType.PROJECT],
        group_reference=GroupReference.SHORT_NAME,
    )

    @classmethod
    def capabilities(cls) -> FilterCapabilityDescriptor:
        return cls._caps

    @classmethod
    def validate_query(cls, query: str) -> str | None:
        if "reject-me" in query:
            return "query rejected for test"
        return None

    @classmethod
    def has_self_reference(cls, query: str) -> bool:
        return False

    @classmethod
    def substitute_identity(cls, query: str, source_user_id: str) -> str:
        return query

    async def execute(self, query, client, page) -> FilteredSearchUniverse:
        return FilteredSearchUniverse(
            connector_type="FAKE", records=[], native_query=query, total_available=0, truncated=False,
        )


@pytest.fixture(autouse=True)
def _register_fake_adapter():
    original = FilterAdapterRegistry._adapters.get("FAKE")
    FilterAdapterRegistry.register("FAKE", _FakeAdapter)
    yield
    if original is not None:
        FilterAdapterRegistry._adapters["FAKE"] = original
    else:
        FilterAdapterRegistry._adapters.pop("FAKE", None)


class TestRunFilteredSearch:
    async def test_unregistered_connector_type_returns_error(self) -> None:
        ok, payload = await run_filtered_search(
            state={}, connector_id="c1", connector_type="NOPE", client=MagicMock(), query="project = X",
        )
        assert ok is False
        assert "No filter adapter registered" in json.loads(payload)["error"]

    async def test_query_failing_validation_returns_error_without_executing(self, monkeypatch) -> None:
        execute_mock = AsyncMock()
        monkeypatch.setattr(_FakeAdapter, "execute", execute_mock)
        ok, payload = await run_filtered_search(
            state={}, connector_id="c1", connector_type="FAKE", client=MagicMock(), query="reject-me",
        )
        assert ok is False
        assert "query rejected for test" in json.loads(payload)["error"]
        execute_mock.assert_not_called()

    async def test_missing_graph_provider_returns_error(self) -> None:
        ok, payload = await run_filtered_search(
            state={}, connector_id="c1", connector_type="FAKE", client=MagicMock(), query="project = X",
        )
        assert ok is False
        assert "Graph provider" in json.loads(payload)["error"]

    async def test_successful_search_returns_gated_records_with_virtual_ids(self, monkeypatch) -> None:
        from app.agents.actions.filtered_search.bridge import BridgedResult
        from app.config.constants.arangodb import Connectors, OriginTypes
        from app.models.entities import Record, RecordType

        record = Record(
            id="internal-1", record_name="Issue 1", record_type=RecordType.TICKET,
            external_record_id="E1", version=1, origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.JIRA, connector_id="c1", virtual_record_id="vr-1",
            weburl="https://x/1",
        )

        monkeypatch.setattr(execution_module, "get_user_key", AsyncMock(return_value="user-key"))
        bridge_instance = MagicMock()
        bridge_instance.resolve_and_gate = AsyncMock(return_value=BridgedResult(
            accessible_records=[record], denied_count=1, unresolved_external_ids=["E9"],
        ))
        monkeypatch.setattr(execution_module, "FilteredRetrievalBridge", MagicMock(return_value=bridge_instance))

        state = {"graph_provider": MagicMock(), "org_id": "org1", "user_id": "u1"}
        ok, payload = await run_filtered_search(
            state=state, connector_id="c1", connector_type="FAKE", client=MagicMock(), query="project = X",
        )

        assert ok is True
        data = json.loads(payload)
        assert data["accessible_count"] == 1
        assert data["denied_count"] == 1
        assert data["unresolved_count"] == 1
        assert data["records"][0]["virtual_record_id"] == "vr-1"
        assert data["records"][0]["external_id"] == "E1"

    async def test_user_not_found_returns_error(self, monkeypatch) -> None:
        monkeypatch.setattr(execution_module, "get_user_key", AsyncMock(return_value=None))
        state = {"graph_provider": MagicMock(), "org_id": "org1", "user_id": "u1"}
        ok, payload = await run_filtered_search(
            state=state, connector_id="c1", connector_type="FAKE", client=MagicMock(), query="project = X",
        )
        assert ok is False
        assert "User not found" in json.loads(payload)["error"]

    async def test_adapter_execute_exception_is_reported_not_raised(self, monkeypatch) -> None:
        class _FailingAdapter(_FakeAdapter):
            async def execute(self, query, client, page):
                raise RuntimeError("api down")

        FilterAdapterRegistry.register("FAKE", _FailingAdapter)
        ok, payload = await run_filtered_search(
            state={"graph_provider": MagicMock()}, connector_id="c1", connector_type="FAKE",
            client=MagicMock(), query="project = X",
        )
        assert ok is False
        assert "Native filter search failed" in json.loads(payload)["error"]
