"""Unit tests for ``OntologyRegistryStore`` (KG Clean Rebuild plan, Phase 7)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.knowledge_graph.contracts.ontology import OntologyStatus
from app.modules.knowledge_graph.governance.ontology_store import (
    OntologyGovernanceError,
    OntologyRegistryStore,
)


@pytest.fixture
def graph_provider() -> MagicMock:
    provider = MagicMock()
    provider.get_nodes_by_filters = AsyncMock(return_value=[])
    provider.get_document = AsyncMock(return_value=None)
    provider.batch_upsert_nodes = AsyncMock(return_value=True)
    return provider


@pytest.fixture
def store(graph_provider) -> OntologyRegistryStore:
    return OntologyRegistryStore(graph_provider, MagicMock())


class TestGetActiveOntology:
    @pytest.mark.asyncio
    async def test_no_match_returns_none(self, store, graph_provider) -> None:
        result = await store.get_active_ontology("org-1", "legal", "contract")

        assert result is None
        graph_provider.get_nodes_by_filters.assert_awaited_once_with(
            "kgOntologies", {"orgId": "org-1", "domain": "legal", "status": "active"},
        )

    @pytest.mark.asyncio
    async def test_returns_definition_from_row(self, store, graph_provider) -> None:
        graph_provider.get_nodes_by_filters = AsyncMock(return_value=[{
            "id": "legal-v2", "orgId": "org-1", "domain": "legal",
            "version": "1.0.0", "status": "active", "entity_types": [], "relationship_types": [],
        }])

        result = await store.get_active_ontology("org-1", "legal", "contract")

        assert result is not None
        assert result.ontology_id == "legal-v2"
        assert result.status == OntologyStatus.ACTIVE


class TestPromoteType:
    @pytest.mark.asyncio
    async def test_missing_args_raises(self, store) -> None:
        with pytest.raises(OntologyGovernanceError):
            await store.promote_type("", "legal", "Complaint")

    @pytest.mark.asyncio
    async def test_creates_new_draft_ontology_when_none_exists(self, store, graph_provider) -> None:
        definition = await store.promote_type("org-1", "marketing", "Complaint", description="A complaint")

        assert definition.status == OntologyStatus.DRAFT
        assert definition.entity_type_names() == {"Complaint"}
        graph_provider.batch_upsert_nodes.assert_awaited_once()
        saved_doc = graph_provider.batch_upsert_nodes.call_args[0][0][0]
        assert saved_doc["id"] == "marketing-default"
        assert saved_doc["orgId"] == "org-1"

    @pytest.mark.asyncio
    async def test_adds_type_to_existing_ontology(self, store, graph_provider) -> None:
        graph_provider.get_document = AsyncMock(return_value={
            "id": "marketing-default", "orgId": "org-1", "domain": "marketing",
            "version": "0.1.0", "status": "draft",
            "entity_types": [{"name": "Campaign", "description": "", "attributes": []}],
            "relationship_types": [],
        })

        definition = await store.promote_type("org-1", "marketing", "Complaint")

        assert definition.entity_type_names() == {"Campaign", "Complaint"}

    @pytest.mark.asyncio
    async def test_duplicate_type_name_raises(self, store, graph_provider) -> None:
        graph_provider.get_document = AsyncMock(return_value={
            "id": "marketing-default", "orgId": "org-1", "domain": "marketing",
            "version": "0.1.0", "status": "draft",
            "entity_types": [{"name": "Campaign", "description": "", "attributes": []}],
            "relationship_types": [],
        })

        with pytest.raises(OntologyGovernanceError):
            await store.promote_type("org-1", "marketing", "Campaign")


class TestDeprecateType:
    @pytest.mark.asyncio
    async def test_unknown_ontology_raises(self, store, graph_provider) -> None:
        graph_provider.get_document = AsyncMock(return_value=None)

        with pytest.raises(OntologyGovernanceError):
            await store.deprecate_type("org-1", "missing-ont", "Campaign")

    @pytest.mark.asyncio
    async def test_unknown_type_raises(self, store, graph_provider) -> None:
        graph_provider.get_document = AsyncMock(return_value={
            "id": "marketing-default", "orgId": "org-1", "domain": "marketing",
            "version": "0.1.0", "status": "active",
            "entity_types": [{"name": "Campaign", "description": "", "attributes": []}],
            "relationship_types": [],
        })

        with pytest.raises(OntologyGovernanceError):
            await store.deprecate_type("org-1", "marketing-default", "Nonexistent")

    @pytest.mark.asyncio
    async def test_removes_type_leaves_others(self, store, graph_provider) -> None:
        graph_provider.get_document = AsyncMock(return_value={
            "id": "marketing-default", "orgId": "org-1", "domain": "marketing",
            "version": "0.1.0", "status": "active",
            "entity_types": [
                {"name": "Campaign", "description": "", "attributes": []},
                {"name": "Complaint", "description": "", "attributes": []},
            ],
            "relationship_types": [],
        })

        definition = await store.deprecate_type("org-1", "marketing-default", "Complaint")

        assert definition.entity_type_names() == {"Campaign"}


class TestUpdateStatus:
    @pytest.mark.asyncio
    async def test_activates_draft_ontology(self, store, graph_provider) -> None:
        graph_provider.get_document = AsyncMock(return_value={
            "id": "marketing-default", "orgId": "org-1", "domain": "marketing",
            "version": "0.1.0", "status": "draft", "entity_types": [], "relationship_types": [],
        })

        definition = await store.update_status("org-1", "marketing-default", OntologyStatus.ACTIVE)

        assert definition.status == OntologyStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_unknown_ontology_raises(self, store, graph_provider) -> None:
        graph_provider.get_document = AsyncMock(return_value=None)

        with pytest.raises(OntologyGovernanceError):
            await store.update_status("org-1", "missing", OntologyStatus.ACTIVE)


class TestListOntologies:
    @pytest.mark.asyncio
    async def test_empty_org_id_returns_empty(self, store, graph_provider) -> None:
        result = await store.list_ontologies("")

        assert result == []
        graph_provider.get_nodes_by_filters.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_malformed_rows(self, store, graph_provider) -> None:
        graph_provider.get_nodes_by_filters = AsyncMock(return_value=[
            {"id": "good", "orgId": "org-1", "domain": "legal", "version": "1.0.0",
             "status": "active", "entity_types": [], "relationship_types": []},
            {"id": "bad", "orgId": "org-1", "version": "not-a-semver"},
        ])

        result = await store.list_ontologies("org-1")

        assert len(result) == 1
        assert result[0].ontology_id == "good"
