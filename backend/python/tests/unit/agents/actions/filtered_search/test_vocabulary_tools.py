"""Unit tests for the `FilterVocabulary` toolset: `list_filter_values`
(incl. the roles-not-tracked and stub-shortName-exposure shapes) and
`people_search`."""

import json
from unittest.mock import AsyncMock, MagicMock

from app.agents.actions.filtered_search.vocabulary import PersonVocabEntry, RecordGroupVocabEntry
from app.agents.actions.filtered_search.vocabulary_tools import FilterVocabulary


def _make_tool(state: dict) -> FilterVocabulary:
    tool = FilterVocabulary.__new__(FilterVocabulary)
    tool.state = state
    return tool


class TestListFilterValues:
    async def test_invalid_dimension_returns_error(self) -> None:
        tool = _make_tool({"graph_provider": MagicMock()})
        success, payload = await tool.list_filter_values("c1", "not_a_dimension")
        assert success is False
        assert "Invalid dimension" in json.loads(payload)["error"]

    async def test_no_graph_provider_returns_error(self) -> None:
        tool = _make_tool({})
        success, payload = await tool.list_filter_values("c1", "record_groups")
        assert success is False

    async def test_record_groups_dimension_exposes_key_and_stub_flag(self, monkeypatch) -> None:
        tool = _make_tool({"graph_provider": MagicMock(), "org_id": "org1"})
        monkeypatch.setattr(
            "app.agents.actions.filtered_search.vocabulary_tools.resolve_connector_type",
            AsyncMock(return_value="JIRA"),
        )
        entries = [
            RecordGroupVocabEntry(name="Core", key="CORE", external_id="1", group_type="PROJECT", is_stub=False),
            RecordGroupVocabEntry(name="10099", key="10099", external_id="10099", group_type="PROJECT", is_stub=True),
        ]
        monkeypatch.setattr(
            "app.agents.actions.filtered_search.vocabulary.FilterVocabularyService.record_groups",
            AsyncMock(return_value=entries),
        )

        success, payload = await tool.list_filter_values("c1", "record_groups")

        assert success is True
        data = json.loads(payload)
        assert data["count"] == 2
        assert data["values"][0]["key"] == "CORE"
        assert data["values"][0]["is_stub"] is False
        assert data["values"][1]["is_stub"] is True

    async def test_roles_not_tracked_reports_tracked_false_not_empty(self, monkeypatch) -> None:
        tool = _make_tool({"graph_provider": MagicMock(), "org_id": "org1"})
        monkeypatch.setattr(
            "app.agents.actions.filtered_search.vocabulary_tools.resolve_connector_type",
            AsyncMock(return_value="CONFLUENCE"),
        )
        monkeypatch.setattr(
            "app.agents.actions.filtered_search.vocabulary.FilterVocabularyService.roles",
            AsyncMock(return_value=None),
        )

        success, payload = await tool.list_filter_values("c1", "roles")

        assert success is True
        data = json.loads(payload)
        assert data["tracked"] is False
        assert data["values"] == []

    async def test_roles_tracked_returns_values(self, monkeypatch) -> None:
        tool = _make_tool({"graph_provider": MagicMock(), "org_id": "org1"})
        monkeypatch.setattr(
            "app.agents.actions.filtered_search.vocabulary_tools.resolve_connector_type",
            AsyncMock(return_value="JIRA"),
        )
        monkeypatch.setattr(
            "app.agents.actions.filtered_search.vocabulary.FilterVocabularyService.roles",
            AsyncMock(return_value=[{"name": "Admin", "key": "1", "external_id": "1"}]),
        )

        success, payload = await tool.list_filter_values("c1", "roles")

        data = json.loads(payload)
        assert data["tracked"] is True
        assert data["count"] == 1

    async def test_exception_from_vocab_returns_error_not_raise(self, monkeypatch) -> None:
        tool = _make_tool({"graph_provider": MagicMock(), "org_id": "org1"})
        monkeypatch.setattr(
            "app.agents.actions.filtered_search.vocabulary_tools.resolve_connector_type",
            AsyncMock(return_value="JIRA"),
        )
        monkeypatch.setattr(
            "app.agents.actions.filtered_search.vocabulary.FilterVocabularyService.record_groups",
            AsyncMock(side_effect=RuntimeError("boom")),
        )

        success, payload = await tool.list_filter_values("c1", "record_groups")

        assert success is False
        assert "boom" in json.loads(payload)["error"]


class TestPeopleSearch:
    async def test_no_graph_provider_returns_error(self) -> None:
        tool = _make_tool({})
        success, _ = await tool.people_search("alice")
        assert success is False

    async def test_searches_only_filter_capable_connectors(self, monkeypatch) -> None:
        from app.agents.actions.knowledge_graph.catalog import ConnectorInfo

        tool = _make_tool({"graph_provider": MagicMock(), "org_id": "org1", "user_id": "u1"})
        monkeypatch.setattr(
            "app.agents.actions.filtered_search.vocabulary_tools.get_user_key",
            AsyncMock(return_value="key1"),
        )

        catalog = MagicMock()
        catalog.connectors = [
            ConnectorInfo(id="c1", name="Jira", type="JIRA"),
            ConnectorInfo(id="c2", name="Some KB", type="KB"),
        ]

        async def _build(*args, **kwargs):
            return catalog

        monkeypatch.setattr(
            "app.agents.actions.knowledge_graph.catalog.ConnectorCatalog.build", _build,
        )
        monkeypatch.setattr(
            "app.agents.actions.filtered_search.registry.FilterAdapterRegistry.is_registered",
            lambda ct: ct == "JIRA",
        )
        monkeypatch.setattr(
            "app.agents.actions.filtered_search.registry.FilterAdapterRegistry.get",
            lambda ct: MagicMock(capabilities=lambda: MagicMock(people_coverage_note=None)) if ct == "JIRA" else None,
        )
        monkeypatch.setattr(
            "app.agents.actions.filtered_search.vocabulary.FilterVocabularyService.people",
            AsyncMock(return_value=[PersonVocabEntry(display_name="Alice", email="a@x.com", source_user_id="U1", user_id="k1")]),
        )

        success, payload = await tool.people_search("alice")

        assert success is True
        data = json.loads(payload)
        assert set(data["results"].keys()) == {"c1"}
        assert data["results"]["c1"]["matches"][0]["source_user_id"] == "U1"
