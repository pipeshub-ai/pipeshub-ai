"""Unit tests for ConnectorCatalog — pure and async (lazy build path)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.actions.knowledge_graph.catalog import (
    ConnectorCatalog,
    ConnectorInfo,
    _normalize_token,
    _types_overlap,
)


# ---------------------------------------------------------------------------
# _normalize_token / _types_overlap
# ---------------------------------------------------------------------------


class TestNormalizeToken:
    def test_strips_underscores(self):
        assert _normalize_token("GOOGLE_DRIVE") == "GOOGLEDRIVE"

    def test_strips_spaces(self):
        assert _normalize_token("Jira Cloud") == "JIRACLOUD"

    def test_uppercases(self):
        assert _normalize_token("confluence") == "CONFLUENCE"


class TestTypesOverlap:
    def test_exact_match(self):
        assert _types_overlap("JIRA", "JIRA")

    def test_google_drive_aliases(self):
        assert _types_overlap("DRIVE", "GOOGLE_DRIVE")
        assert _types_overlap("GOOGLEDRIVE", "GOOGLE_DRIVE")
        assert _types_overlap("GOOGLE_DRIVE", "DRIVE")

    def test_partial_match(self):
        assert _types_overlap("JIRA CLOUD", "JIRA")

    def test_no_match(self):
        assert not _types_overlap("CONFLUENCE", "JIRA")


# ---------------------------------------------------------------------------
# ConnectorCatalog.filter
# ---------------------------------------------------------------------------


def _catalog(*entries: tuple[str, str, str]) -> ConnectorCatalog:
    return ConnectorCatalog([
        ConnectorInfo(id=e[0], name=e[1], type=e[2])
        for e in entries
    ])


class TestCatalogFilter:
    def test_no_hint_returns_all(self):
        cat = _catalog(("c1", "Jira Cloud", "JIRA"), ("c2", "Confluence", "CONFLUENCE"))
        assert len(cat.filter()) == 2

    def test_hint_by_type_restricts(self):
        cat = _catalog(("c1", "Jira Cloud", "JIRA"), ("c2", "Confluence", "CONFLUENCE"))
        result = cat.filter(hint="JIRA")
        assert len(result) == 1
        assert result[0].id == "c1"

    def test_hint_by_name(self):
        cat = _catalog(("c1", "Jira Cloud", "JIRA"), ("c2", "Confluence Connector", "CONFLUENCE"))
        result = cat.filter(hint="Confluence Connector")
        assert len(result) == 1
        assert result[0].id == "c2"

    def test_hint_matching_nothing_widens_to_all(self):
        """A hint that matches nothing must NOT dead-end; return all connectors."""
        cat = _catalog(("c1", "Jira Cloud", "JIRA"), ("c2", "Confluence", "CONFLUENCE"))
        result = cat.filter(hint="SLACK")
        assert len(result) == 2

    def test_loose_match_google_drive(self):
        cat = _catalog(("d1", "Google Drive", "DRIVE"))
        result = cat.filter(hint="GOOGLE_DRIVE")
        assert len(result) == 1
        assert result[0].id == "d1"

    def test_filter_by_ids(self):
        cat = _catalog(("c1", "Jira", "JIRA"), ("c2", "Conf", "CONFLUENCE"), ("c3", "Slack", "SLACK"))
        result = cat.filter(ids=["c1", "c3"])
        assert {r.id for r in result} == {"c1", "c3"}

    def test_prefer_ids_sorts_first(self):
        cat = _catalog(("c1", "Jira", "JIRA"), ("c2", "Conf", "CONFLUENCE"), ("c3", "Slack", "SLACK"))
        result = cat.filter(prefer_ids=["c3"])
        assert result[0].id == "c3"

    def test_empty_catalog(self):
        cat = _catalog()
        assert cat.filter() == []
        assert cat.filter(hint="JIRA") == []

    def test_types_deduplicated(self):
        cat = _catalog(("c1", "Jira1", "JIRA"), ("c2", "Jira2", "JIRA"), ("c3", "Conf", "CONFLUENCE"))
        types = cat.types()
        assert types.count("JIRA") == 1


# ---------------------------------------------------------------------------
# ConnectorCatalog.build — source priority
# ---------------------------------------------------------------------------


class TestCatalogBuild:
    @pytest.mark.asyncio
    async def test_builds_from_available_connectors(self):
        state = {
            "available_connectors": [
                {"id": "c1", "name": "Jira Cloud", "type": "JIRA"},
            ]
        }
        gp = AsyncMock()
        cat = await ConnectorCatalog.build(state, graph_provider=gp, user_key="uk", org_id="org")
        assert len(cat) == 1
        assert cat.connectors[0].type == "JIRA"
        gp.get_knowledge_hub_filter_options.assert_not_called()

    @pytest.mark.asyncio
    async def test_builds_from_agent_knowledge(self):
        state = {
            "agent_knowledge": [
                {"connectorId": "c1", "type": "CONFLUENCE", "name": "Conf Connector"},
            ]
        }
        gp = AsyncMock()
        cat = await ConnectorCatalog.build(state, graph_provider=gp, user_key="uk", org_id="org")
        assert len(cat) == 1
        assert cat.connectors[0].type == "CONFLUENCE"

    @pytest.mark.asyncio
    async def test_lazy_fallback_when_empty(self):
        state: dict = {}
        gp = AsyncMock()
        gp.get_knowledge_hub_filter_options = AsyncMock(return_value={
            "apps": [{"id": "c1", "name": "Jira", "type": "JIRA"}]
        })
        cat = await ConnectorCatalog.build(state, graph_provider=gp, user_key="uk", org_id="org")
        assert len(cat) == 1
        gp.get_knowledge_hub_filter_options.assert_called_once()

    @pytest.mark.asyncio
    async def test_cached_in_state(self):
        state: dict = {
            "available_connectors": [{"id": "c1", "name": "Jira", "type": "JIRA"}]
        }
        gp = AsyncMock()
        cat1 = await ConnectorCatalog.build(state, graph_provider=gp, user_key="uk", org_id="org")
        cat2 = await ConnectorCatalog.build(state, graph_provider=gp, user_key="uk", org_id="org")
        assert cat1 is cat2  # same object returned from cache

    @pytest.mark.asyncio
    async def test_lazy_fallback_failure_returns_empty(self):
        state: dict = {}
        gp = AsyncMock()
        gp.get_knowledge_hub_filter_options = AsyncMock(side_effect=RuntimeError("db down"))
        cat = await ConnectorCatalog.build(state, graph_provider=gp, user_key="uk", org_id="org")
        assert cat.is_empty()

    @pytest.mark.asyncio
    async def test_deduplicates_across_sources(self):
        """Same connector id from both available_connectors and agent_knowledge → one entry."""
        state = {
            "available_connectors": [{"id": "c1", "name": "Jira", "type": "JIRA"}],
            "agent_knowledge": [{"connectorId": "c1", "type": "JIRA", "name": "Jira Cloud"}],
        }
        gp = AsyncMock()
        cat = await ConnectorCatalog.build(state, graph_provider=gp, user_key="uk", org_id="org")
        assert len(cat) == 1
