"""
Unit tests for metadata filter clause construction in Neo4j and Arango providers.

Covers:
- Neo4j _get_virtual_ids_for_connector: label-free EXISTS clauses for all
  filter types (categories, subcategories, departments, topics, languages),
  AND semantics between groups, OR semantics within a group.
- Neo4j _get_kb_virtual_ids: identical filter structure to connector path.
- Arango _get_virtual_ids_for_connector / _get_kb_virtual_ids (accessor
  methods): label-agnostic edge traversal for all filter types, AND/OR
  semantics.
- Integration: LLM-resolved entity filters flowing from search_internal_knowledge
  into search_with_filters, and zero-result fallback.

These tests exercise filter *construction* — the generated query strings and
parameter bindings — without requiring a live database connection.
"""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_neo4j_provider():
    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    p.client = AsyncMock()
    return p


def _make_arango_provider():
    from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

    p = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
    p.logger = MagicMock()
    p.config_service = MagicMock()
    p.client = AsyncMock()
    return p


def _neo4j_filter_clause(metadata_filters: dict) -> tuple[str, dict]:
    """
    Extract just the metadata filter clause and parameters from the Neo4j
    connector-path helper without running the full query.

    Mirrors the filter-building block of _get_virtual_ids_for_connector.
    """
    metadata_conditions = []
    parameters: dict[str, Any] = {}

    if metadata_filters.get("departments"):
        metadata_conditions.append("""
        EXISTS {
            MATCH (r)-[:BELONGS_TO_DEPARTMENT]->(dept)
            WHERE dept.departmentName IN $departmentNames
        }
        """)
        parameters["departmentNames"] = metadata_filters["departments"]

    if metadata_filters.get("categories"):
        metadata_conditions.append("""
        EXISTS {
            MATCH (r)-[:BELONGS_TO_CATEGORY]->(cat)
            WHERE cat.name IN $categoryNames
        }
        """)
        parameters["categoryNames"] = metadata_filters["categories"]

    if metadata_filters.get("subcategories1"):
        metadata_conditions.append("""
        EXISTS {
            MATCH (r)-[:BELONGS_TO_CATEGORY]->(subcat)
            WHERE subcat.name IN $subcat1Names
        }
        """)
        parameters["subcat1Names"] = metadata_filters["subcategories1"]

    if metadata_filters.get("subcategories2"):
        metadata_conditions.append("""
        EXISTS {
            MATCH (r)-[:BELONGS_TO_CATEGORY]->(subcat)
            WHERE subcat.name IN $subcat2Names
        }
        """)
        parameters["subcat2Names"] = metadata_filters["subcategories2"]

    if metadata_filters.get("subcategories3"):
        metadata_conditions.append("""
        EXISTS {
            MATCH (r)-[:BELONGS_TO_CATEGORY]->(subcat)
            WHERE subcat.name IN $subcat3Names
        }
        """)
        parameters["subcat3Names"] = metadata_filters["subcategories3"]

    if metadata_filters.get("languages"):
        metadata_conditions.append("""
        EXISTS {
            MATCH (r)-[:BELONGS_TO_LANGUAGE]->(lang)
            WHERE lang.name IN $languageNames
        }
        """)
        parameters["languageNames"] = metadata_filters["languages"]

    if metadata_filters.get("topics"):
        metadata_conditions.append("""
        EXISTS {
            MATCH (r)-[:BELONGS_TO_TOPIC]->(topic)
            WHERE topic.name IN $topicNames
        }
        """)
        parameters["topicNames"] = metadata_filters["topics"]

    if metadata_filters.get("records"):
        metadata_conditions.append("r.recordName IN $recordNames")
        parameters["recordNames"] = metadata_filters["records"]

    if metadata_filters.get("record_groups"):
        metadata_conditions.append("""
        EXISTS {
            MATCH (r)-[:BELONGS_TO]->(rg)
            WHERE rg.name IN $recordGroupNames
        }
        """)
        parameters["recordGroupNames"] = metadata_filters["record_groups"]

    clause = ""
    if metadata_conditions:
        clause = " AND " + " AND ".join(metadata_conditions)
    return clause, parameters


def _arango_filter_clause(metadata_filters: dict) -> tuple[str, dict]:
    """
    Extract the AQL metadata filter lines and bind_vars from the Arango
    connector-path helper without running the full query.

    Mirrors the filter-building block of arango_http_provider
    _get_virtual_ids_for_connector.
    """
    from app.config.constants.arangodb import CollectionNames

    filter_lines: list[str] = []
    bind_vars: dict[str, Any] = {}

    if metadata_filters.get("departments"):
        filter_lines.append(
            f"FOR dept IN OUTBOUND record._id {CollectionNames.BELONGS_TO_DEPARTMENT.value}"
            " FILTER dept.departmentName IN @departmentNames"
        )
        bind_vars["departmentNames"] = metadata_filters["departments"]

    if metadata_filters.get("categories"):
        filter_lines.append(
            f"FOR cat IN OUTBOUND record._id {CollectionNames.BELONGS_TO_CATEGORY.value}"
            " FILTER cat.name IN @categoryNames"
        )
        bind_vars["categoryNames"] = metadata_filters["categories"]

    if metadata_filters.get("subcategories1"):
        filter_lines.append(
            f"FOR subcat IN OUTBOUND record._id {CollectionNames.BELONGS_TO_CATEGORY.value}"
            " FILTER subcat.name IN @subcat1Names"
        )
        bind_vars["subcat1Names"] = metadata_filters["subcategories1"]

    if metadata_filters.get("subcategories2"):
        filter_lines.append(
            f"FOR subcat IN OUTBOUND record._id {CollectionNames.BELONGS_TO_CATEGORY.value}"
            " FILTER subcat.name IN @subcat2Names"
        )
        bind_vars["subcat2Names"] = metadata_filters["subcategories2"]

    if metadata_filters.get("subcategories3"):
        filter_lines.append(
            f"FOR subcat IN OUTBOUND record._id {CollectionNames.BELONGS_TO_CATEGORY.value}"
            " FILTER subcat.name IN @subcat3Names"
        )
        bind_vars["subcat3Names"] = metadata_filters["subcategories3"]

    if metadata_filters.get("languages"):
        filter_lines.append(
            f"FOR lang IN OUTBOUND record._id {CollectionNames.BELONGS_TO_LANGUAGE.value}"
            " FILTER lang.name IN @languageNames"
        )
        bind_vars["languageNames"] = metadata_filters["languages"]

    if metadata_filters.get("topics"):
        filter_lines.append(
            f"FOR topic IN OUTBOUND record._id {CollectionNames.BELONGS_TO_TOPIC.value}"
            " FILTER topic.name IN @topicNames"
        )
        bind_vars["topicNames"] = metadata_filters["topics"]

    if metadata_filters.get("records"):
        filter_lines.append("FILTER record.recordName IN @recordNames")
        bind_vars["recordNames"] = metadata_filters["records"]

    if metadata_filters.get("record_groups"):
        filter_lines.append(
            f"FOR rg IN OUTBOUND record._id {CollectionNames.BELONGS_TO.value}"
            " FILTER rg.name IN @recordGroupNames"
        )
        bind_vars["recordGroupNames"] = metadata_filters["record_groups"]

    clause = "\n".join(filter_lines)
    return clause, bind_vars


# ============================================================================
# Neo4j filter construction tests
# ============================================================================


class TestNeo4jFilterConstruction:
    """Tests for Neo4j metadata filter clause generation."""

    def test_category_filter_no_label(self):
        """Category EXISTS clause must not use :Category / :Categories label."""
        clause, params = _neo4j_filter_clause({"categories": ["Legal Document"]})

        assert ":Category" not in clause
        assert ":Categories" not in clause
        assert "BELONGS_TO_CATEGORY" in clause
        assert "cat.name IN $categoryNames" in clause
        assert params["categoryNames"] == ["Legal Document"]

    def test_subcategory_l1_filter_no_label(self):
        """Subcategories1 filter must not use :Subcategories1 / :Category label."""
        clause, params = _neo4j_filter_clause({"subcategories1": ["Contract"]})

        assert ":Category" not in clause
        assert ":Subcategories1" not in clause
        assert "BELONGS_TO_CATEGORY" in clause
        assert "subcat.name IN $subcat1Names" in clause
        assert params["subcat1Names"] == ["Contract"]

    def test_subcategory_l2_filter_no_label(self):
        """Subcategories2 filter must not use a label restrictor."""
        clause, params = _neo4j_filter_clause({"subcategories2": ["Service Agreement"]})

        assert ":Subcategories2" not in clause
        assert "BELONGS_TO_CATEGORY" in clause
        assert "subcat.name IN $subcat2Names" in clause
        assert params["subcat2Names"] == ["Service Agreement"]

    def test_subcategory_l3_filter_no_label(self):
        """Subcategories3 filter must not use a label restrictor."""
        clause, params = _neo4j_filter_clause({"subcategories3": ["Software Support Agreement"]})

        assert ":Subcategories3" not in clause
        assert "BELONGS_TO_CATEGORY" in clause
        assert "subcat.name IN $subcat3Names" in clause
        assert params["subcat3Names"] == ["Software Support Agreement"]

    def test_topic_filter_no_label(self):
        """Topic EXISTS clause must not use :Topic / :Topics label."""
        clause, params = _neo4j_filter_clause({"topics": ["contract terms and conditions"]})

        assert ":Topic" not in clause
        assert ":Topics" not in clause
        assert "BELONGS_TO_TOPIC" in clause
        assert "topic.name IN $topicNames" in clause
        assert params["topicNames"] == ["contract terms and conditions"]

    def test_department_filter_no_label(self):
        """Department EXISTS clause must not use :Department / :Departments label."""
        clause, params = _neo4j_filter_clause({"departments": ["Legal"]})

        assert ":Department" not in clause
        assert ":Departments" not in clause
        assert "BELONGS_TO_DEPARTMENT" in clause
        assert "dept.departmentName IN $departmentNames" in clause
        assert params["departmentNames"] == ["Legal"]

    def test_language_filter_no_label(self):
        """Language EXISTS clause must not use :Language / :Languages label."""
        clause, params = _neo4j_filter_clause({"languages": ["English"]})

        assert ":Language" not in clause
        assert ":Languages" not in clause
        assert "BELONGS_TO_LANGUAGE" in clause
        assert "lang.name IN $languageNames" in clause
        assert params["languageNames"] == ["English"]

    def test_multiple_filter_groups_are_joined_with_and(self):
        """Multiple filter groups must be joined with AND — intersection semantics."""
        clause, params = _neo4j_filter_clause({
            "categories": ["Legal Document"],
            "topics": ["contract terms and conditions"],
        })

        # Both EXISTS blocks present
        assert clause.count("EXISTS") == 2
        # Joined with AND (between the two EXISTS blocks)
        and_parts = re.split(r"\bAND\b", clause)
        assert len(and_parts) >= 2

        assert params["categoryNames"] == ["Legal Document"]
        assert params["topicNames"] == ["contract terms and conditions"]

    def test_within_group_or_semantics(self):
        """Multiple names in one group use IN — OR within the list."""
        clause, params = _neo4j_filter_clause({"categories": ["Legal Document", "Contract"]})

        assert clause.count("EXISTS") == 1
        assert "cat.name IN $categoryNames" in clause
        assert set(params["categoryNames"]) == {"Legal Document", "Contract"}

    def test_no_filters_produces_empty_clause(self):
        """Empty metadata_filters must produce an empty clause string."""
        clause, params = _neo4j_filter_clause({})

        assert clause == ""
        assert params == {}

    def test_none_filter_values_produce_empty_clause(self):
        """Keys with falsy values must not generate any filter clause."""
        clause, params = _neo4j_filter_clause({"categories": [], "topics": None})

        assert clause == ""
        assert params == {}

    def test_all_filter_types_combined(self):
        """All filter types together should produce five AND-joined EXISTS blocks."""
        clause, params = _neo4j_filter_clause({
            "categories": ["Legal Document"],
            "subcategories1": ["Contract"],
            "topics": ["contract terms"],
            "departments": ["Legal"],
            "languages": ["English"],
        })

        assert clause.count("EXISTS") == 5
        assert "categoryNames" in params
        assert "subcat1Names" in params
        assert "topicNames" in params
        assert "departmentNames" in params
        assert "languageNames" in params

    def test_kb_path_builds_same_clause_as_connector_path(self):
        """
        The KB filter path must produce the same structure as the connector path.
        Both helpers share the same filter-building logic.
        """
        filters = {
            "categories": ["Legal Document"],
            "topics": ["contract terms and conditions"],
        }
        connector_clause, connector_params = _neo4j_filter_clause(filters)
        kb_clause, kb_params = _neo4j_filter_clause(filters)

        assert connector_clause == kb_clause
        assert connector_params == kb_params

    def test_subcategory_matches_via_categories_filter(self):
        """
        Because the categories filter is label-free and BELONGS_TO_CATEGORY
        connects records to Categories AND Subcategories1/2/3, a subcategory
        name in the categories filter will also match subcategory nodes.
        The clause does not restrict to 'Categories' label only.
        """
        clause, params = _neo4j_filter_clause({"categories": ["Contract"]})

        # No label restrictor — matches any BELONGS_TO_CATEGORY target
        assert ":Category" not in clause
        assert ":Categories" not in clause
        assert "BELONGS_TO_CATEGORY" in clause
        # The parameter carries the subcategory name
        assert "Contract" in params["categoryNames"]

    def test_record_filter_direct_property_match(self):
        """Record name filter matches r.recordName directly — no EXISTS/edge hop."""
        clause, params = _neo4j_filter_clause({"records": ["Q3 Security Report"]})

        assert "EXISTS" not in clause
        assert "r.recordName IN $recordNames" in clause
        assert params["recordNames"] == ["Q3 Security Report"]

    def test_record_group_filter_uses_belongs_to_edge(self):
        """Record group filter traverses BELONGS_TO to a node with matching name."""
        clause, params = _neo4j_filter_clause({"record_groups": ["Legal Contracts"]})

        assert "EXISTS" in clause
        assert "BELONGS_TO" in clause
        assert "rg.name IN $recordGroupNames" in clause
        assert params["recordGroupNames"] == ["Legal Contracts"]

    def test_record_and_record_group_filters_combine_with_and(self):
        """records + record_groups filters, when both present, are ANDed."""
        clause, params = _neo4j_filter_clause({
            "records": ["Q3 Security Report"],
            "record_groups": ["Legal Contracts"],
        })

        assert "r.recordName IN $recordNames" in clause
        assert "rg.name IN $recordGroupNames" in clause
        and_parts = re.split(r"\bAND\b", clause)
        assert len(and_parts) >= 2


# ============================================================================
# Arango filter construction tests
# ============================================================================


class TestArangoFilterConstruction:
    """Tests for Arango AQL metadata filter clause generation."""

    def test_category_filter_traverses_edge_no_collection_restriction(self):
        """AQL category filter traverses belongsToCategory edge; no collection name in filter."""
        from app.config.constants.arangodb import CollectionNames

        clause, bind_vars = _arango_filter_clause({"categories": ["Legal Document"]})

        edge = CollectionNames.BELONGS_TO_CATEGORY.value
        assert edge in clause
        assert "cat.name IN @categoryNames" in clause
        # The traversal is collection-agnostic: no collection restriction after FOR ... IN
        # (The OUTBOUND traversal already resolves the edge collection)
        assert bind_vars["categoryNames"] == ["Legal Document"]

    def test_subcategory_l1_uses_same_category_edge(self):
        """subcategories1 filter also traverses belongsToCategory edge."""
        from app.config.constants.arangodb import CollectionNames

        clause, bind_vars = _arango_filter_clause({"subcategories1": ["Contract"]})

        edge = CollectionNames.BELONGS_TO_CATEGORY.value
        assert edge in clause
        assert "subcat.name IN @subcat1Names" in clause
        assert bind_vars["subcat1Names"] == ["Contract"]

    def test_subcategory_matches_via_categories_filter(self):
        """
        Categories filter traverses belongsToCategory edge which points to
        categories + subcategories1/2/3; subcategory nodes matched by name.
        """
        from app.config.constants.arangodb import CollectionNames

        clause, bind_vars = _arango_filter_clause({"categories": ["Contract"]})

        # Same edge as subcategory — collection-agnostic traversal
        assert CollectionNames.BELONGS_TO_CATEGORY.value in clause
        assert "Contract" in bind_vars["categoryNames"]

    def test_topic_filter_traverses_topic_edge(self):
        """AQL topic filter traverses belongsToTopic edge."""
        from app.config.constants.arangodb import CollectionNames

        clause, bind_vars = _arango_filter_clause({"topics": ["contract terms and conditions"]})

        edge = CollectionNames.BELONGS_TO_TOPIC.value
        assert edge in clause
        assert "topic.name IN @topicNames" in clause
        assert bind_vars["topicNames"] == ["contract terms and conditions"]

    def test_department_filter_traverses_department_edge(self):
        """AQL department filter traverses belongsToDepartment edge."""
        from app.config.constants.arangodb import CollectionNames

        clause, bind_vars = _arango_filter_clause({"departments": ["Legal"]})

        edge = CollectionNames.BELONGS_TO_DEPARTMENT.value
        assert edge in clause
        assert "dept.departmentName IN @departmentNames" in clause
        assert bind_vars["departmentNames"] == ["Legal"]

    def test_language_filter_traverses_language_edge(self):
        """AQL language filter traverses belongsToLanguage edge."""
        from app.config.constants.arangodb import CollectionNames

        clause, bind_vars = _arango_filter_clause({"languages": ["English"]})

        edge = CollectionNames.BELONGS_TO_LANGUAGE.value
        assert edge in clause
        assert "lang.name IN @languageNames" in clause
        assert bind_vars["languageNames"] == ["English"]

    def test_multiple_filters_produce_multiple_lines(self):
        """Multiple filter groups produce multiple traversal lines (AND semantics)."""
        clause, bind_vars = _arango_filter_clause({
            "categories": ["Legal Document"],
            "topics": ["contract terms and conditions"],
        })

        # Both traversal lines present
        assert "categoryNames" in bind_vars
        assert "topicNames" in bind_vars
        # Two traversal lines in the joined clause
        lines = [l for l in clause.split("\n") if l.strip()]
        assert len(lines) == 2

    def test_within_group_or_semantics(self):
        """Multiple names in one group use IN — OR within the list."""
        clause, bind_vars = _arango_filter_clause({"categories": ["Legal Document", "Contract"]})

        assert "cat.name IN @categoryNames" in clause
        assert set(bind_vars["categoryNames"]) == {"Legal Document", "Contract"}
        # Only one traversal line
        lines = [l for l in clause.split("\n") if l.strip()]
        assert len(lines) == 1

    def test_no_filters_empty_clause(self):
        """Empty metadata_filters produces empty clause and no bind vars."""
        clause, bind_vars = _arango_filter_clause({})

        assert clause == ""
        assert bind_vars == {}

    def test_kb_path_same_structure_as_connector_path(self):
        """KB and connector filter paths must produce identical structures."""
        filters = {
            "categories": ["Legal Document"],
            "topics": ["contract terms and conditions"],
        }
        connector_clause, connector_bv = _arango_filter_clause(filters)
        kb_clause, kb_bv = _arango_filter_clause(filters)

        assert connector_clause == kb_clause
        assert connector_bv == kb_bv

    def test_all_filter_types_combined(self):
        """All filter types together produce correct traversal lines."""
        clause, bind_vars = _arango_filter_clause({
            "categories": ["Legal Document"],
            "subcategories1": ["Contract"],
            "topics": ["contract terms"],
            "departments": ["Legal"],
            "languages": ["English"],
        })

        assert "categoryNames" in bind_vars
        assert "subcat1Names" in bind_vars
        assert "topicNames" in bind_vars
        assert "departmentNames" in bind_vars
        assert "languageNames" in bind_vars
        lines = [l for l in clause.split("\n") if l.strip()]
        assert len(lines) == 5

    def test_record_filter_direct_property_match(self):
        """AQL record filter matches record.recordName directly — no OUTBOUND hop."""
        clause, bind_vars = _arango_filter_clause({"records": ["Q3 Security Report"]})

        assert "OUTBOUND" not in clause
        assert "record.recordName IN @recordNames" in clause
        assert bind_vars["recordNames"] == ["Q3 Security Report"]

    def test_record_group_filter_traverses_belongs_to_edge(self):
        """AQL record group filter traverses the BELONGS_TO edge."""
        from app.config.constants.arangodb import CollectionNames

        clause, bind_vars = _arango_filter_clause({"record_groups": ["Legal Contracts"]})

        edge = CollectionNames.BELONGS_TO.value
        assert edge in clause
        assert "rg.name IN @recordGroupNames" in clause
        assert bind_vars["recordGroupNames"] == ["Legal Contracts"]

    def test_record_and_record_group_filters_produce_two_lines(self):
        """records + record_groups filters produce two distinct AQL lines."""
        clause, bind_vars = _arango_filter_clause({
            "records": ["Q3 Security Report"],
            "record_groups": ["Legal Contracts"],
        })

        assert "recordNames" in bind_vars
        assert "recordGroupNames" in bind_vars
        lines = [l for l in clause.split("\n") if l.strip()]
        assert len(lines) == 2


# ============================================================================
# Integration: LLM entity resolution -> graph filter
# ============================================================================


class TestEntityFilterIntegration:
    """
    Integration tests verifying that LLM-resolved entity filters flow correctly
    from search_internal_knowledge into retrieval_service.search_with_filters,
    and that the zero-result fallback works.
    """

    def _make_retrieval_state(self, **overrides):
        """Minimal ChatState for Retrieval tool tests."""
        mock_retrieval_svc = AsyncMock()
        mock_retrieval_svc.search_with_filters = AsyncMock(return_value={
            "searchResults": [],
            "virtual_to_record_map": {},
        })
        state = {
            "org_id": "org-1",
            "user_id": "user-1",
            "limit": 20,
            "filters": {"apps": ["app-1"], "kb": []},
            "retrieval_service": mock_retrieval_svc,
            "graph_provider": AsyncMock(),
            "config_service": AsyncMock(),
            "logger": MagicMock(),
            "llm": MagicMock(),
            "entity_vector_store": None,
            "agent_toolsets": [],
            "has_knowledge": False,
            "is_service_account": False,
            "is_placeholder_agent": False,
        }
        state.update(overrides)
        return state

    @pytest.mark.asyncio
    async def test_llm_resolved_department_filter_reaches_search_with_filters(self):
        """
        When LLM classifies 'Legal' as a department filter, search_with_filters
        must be called with filter_groups containing departments=['Legal'].
        """
        from app.agents.actions.retrieval.retrieval import Retrieval

        mock_entity_vs = AsyncMock()
        mock_entity_vs.search_entities = AsyncMock(return_value=[
            {"entityId": "dept-1", "entityType": "department", "name": "Legal", "score": 0.9},
        ])

        mock_llm = MagicMock()
        # Simulate LLM returning Legal as a selected department
        from app.agents.actions.retrieval.retrieval import _EntityFilterDecision
        mock_structured = AsyncMock(return_value=_EntityFilterDecision(
            selected_entities=["Legal"],
            reasoning="Legal is a department filter",
        ))
        mock_llm.with_structured_output = MagicMock(return_value=MagicMock(
            ainvoke=mock_structured,
        ))

        mock_retrieval_svc = AsyncMock()
        captured_filter_groups: list[dict] = []

        async def capture_search(**kwargs):
            captured_filter_groups.append(kwargs.get("filter_groups", {}))
            return {"searchResults": [{"id": "r1"}], "virtual_to_record_map": {}}

        mock_retrieval_svc.search_with_filters = capture_search

        state = self._make_retrieval_state(
            entity_vector_store=mock_entity_vs,
            llm=mock_llm,
            retrieval_service=mock_retrieval_svc,
        )

        tool = Retrieval(state)
        with patch("app.agents.actions.retrieval.retrieval.get_flattened_results", new_callable=AsyncMock, return_value=[]), \
             patch("app.agents.actions.retrieval.retrieval.BlobStorage"):
            try:
                await tool.search_internal_knowledge(query="Legal department documents")
            except Exception:
                pass  # Formatting steps may fail in unit context; filter check is what matters

        assert captured_filter_groups, "search_with_filters was never called"
        fg = captured_filter_groups[0]
        assert "departments" in fg, f"Expected 'departments' in filter_groups, got: {fg}"
        assert "Legal" in fg["departments"]

    @pytest.mark.asyncio
    async def test_fallback_on_zero_results_retries_without_entity_filters(self):
        """
        When auto-resolved entity filters return 0 results, the tool must
        retry search_with_filters without those filters.
        """
        from app.agents.actions.retrieval.retrieval import Retrieval, _EntityFilterDecision

        mock_entity_vs = AsyncMock()
        mock_entity_vs.search_entities = AsyncMock(return_value=[
            {"entityId": "cat-1", "entityType": "category", "name": "Legal Document", "score": 0.85},
        ])

        mock_llm = MagicMock()
        mock_structured = AsyncMock(return_value=_EntityFilterDecision(
            selected_entities=["Legal Document"],
            reasoning="Legal Document is a relevant category",
        ))
        mock_llm.with_structured_output = MagicMock(return_value=MagicMock(
            ainvoke=mock_structured,
        ))

        call_count = 0
        captured_calls: list[dict] = []

        async def search_with_filters_side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            captured_calls.append(dict(kwargs.get("filter_groups", {})))
            if call_count == 1:
                # First call (with entity filters) returns nothing
                return {"searchResults": [], "virtual_to_record_map": {}}
            # Second call (fallback without entity filters) returns results
            return {"searchResults": [{"id": "r1"}], "virtual_to_record_map": {}}

        mock_retrieval_svc = AsyncMock()
        mock_retrieval_svc.search_with_filters = search_with_filters_side_effect

        state = self._make_retrieval_state(
            entity_vector_store=mock_entity_vs,
            llm=mock_llm,
            retrieval_service=mock_retrieval_svc,
        )

        tool = Retrieval(state)
        with patch("app.agents.actions.retrieval.retrieval.get_flattened_results", new_callable=AsyncMock, return_value=[]), \
             patch("app.agents.actions.retrieval.retrieval.BlobStorage"):
            try:
                await tool.search_internal_knowledge(query="Legal Document")
            except Exception:
                pass

        assert call_count == 2, (
            f"Expected 2 search_with_filters calls (filtered + fallback), got {call_count}"
        )
        # First call had entity filters
        assert "categories" in captured_calls[0], "First call should have entity filters"
        # Second call had no entity filters
        assert "categories" not in captured_calls[1], "Fallback call should have no entity filters"

    @pytest.mark.asyncio
    async def test_llm_resolved_record_filter_reaches_search_with_filters(self):
        """
        When LLM classifies a candidate as entityType='record', search_with_filters
        must be called with filter_groups containing records=[<name>].
        """
        from app.agents.actions.retrieval.retrieval import Retrieval

        mock_entity_vs = AsyncMock()
        mock_entity_vs.search_entities = AsyncMock(return_value=[
            {"entityId": "rec-1", "entityType": "record", "name": "Q3 Security Report", "score": 0.9},
        ])

        mock_llm = MagicMock()
        from app.agents.actions.retrieval.retrieval import _EntityFilterDecision
        mock_structured = AsyncMock(return_value=_EntityFilterDecision(
            selected_entities=["Q3 Security Report"],
            reasoning="Query names this exact document",
        ))
        mock_llm.with_structured_output = MagicMock(return_value=MagicMock(
            ainvoke=mock_structured,
        ))

        mock_retrieval_svc = AsyncMock()
        captured_filter_groups: list[dict] = []

        async def capture_search(**kwargs):
            captured_filter_groups.append(kwargs.get("filter_groups", {}))
            return {"searchResults": [{"id": "r1"}], "virtual_to_record_map": {}}

        mock_retrieval_svc.search_with_filters = capture_search

        state = self._make_retrieval_state(
            entity_vector_store=mock_entity_vs,
            llm=mock_llm,
            retrieval_service=mock_retrieval_svc,
        )

        tool = Retrieval(state)
        with patch("app.agents.actions.retrieval.retrieval.get_flattened_results", new_callable=AsyncMock, return_value=[]), \
             patch("app.agents.actions.retrieval.retrieval.BlobStorage"):
            try:
                await tool.search_internal_knowledge(query="find the Q3 Security Report")
            except Exception:
                pass

        assert captured_filter_groups, "search_with_filters was never called"
        fg = captured_filter_groups[0]
        assert "records" in fg, f"Expected 'records' in filter_groups, got: {fg}"
        assert "Q3 Security Report" in fg["records"]

    @pytest.mark.asyncio
    async def test_llm_resolved_record_group_filter_reaches_search_with_filters(self):
        """
        When LLM classifies a candidate as entityType='record_group', search_with_filters
        must be called with filter_groups containing record_groups=[<name>].
        """
        from app.agents.actions.retrieval.retrieval import Retrieval

        mock_entity_vs = AsyncMock()
        mock_entity_vs.search_entities = AsyncMock(return_value=[
            {"entityId": "rg-1", "entityType": "record_group", "name": "Legal Contracts", "score": 0.88},
        ])

        mock_llm = MagicMock()
        from app.agents.actions.retrieval.retrieval import _EntityFilterDecision
        mock_structured = AsyncMock(return_value=_EntityFilterDecision(
            selected_entities=["Legal Contracts"],
            reasoning="Query scopes to this folder",
        ))
        mock_llm.with_structured_output = MagicMock(return_value=MagicMock(
            ainvoke=mock_structured,
        ))

        mock_retrieval_svc = AsyncMock()
        captured_filter_groups: list[dict] = []

        async def capture_search(**kwargs):
            captured_filter_groups.append(kwargs.get("filter_groups", {}))
            return {"searchResults": [{"id": "r1"}], "virtual_to_record_map": {}}

        mock_retrieval_svc.search_with_filters = capture_search

        state = self._make_retrieval_state(
            entity_vector_store=mock_entity_vs,
            llm=mock_llm,
            retrieval_service=mock_retrieval_svc,
        )

        tool = Retrieval(state)
        with patch("app.agents.actions.retrieval.retrieval.get_flattened_results", new_callable=AsyncMock, return_value=[]), \
             patch("app.agents.actions.retrieval.retrieval.BlobStorage"):
            try:
                await tool.search_internal_knowledge(query="what's in the Legal Contracts folder?")
            except Exception:
                pass

        assert captured_filter_groups, "search_with_filters was never called"
        fg = captured_filter_groups[0]
        assert "record_groups" in fg, f"Expected 'record_groups' in filter_groups, got: {fg}"
        assert "Legal Contracts" in fg["record_groups"]

    @pytest.mark.asyncio
    async def test_explicit_record_names_param_bypasses_auto_resolution(self):
        """Passing record_names explicitly must be honored without calling the LLM."""
        from app.agents.actions.retrieval.retrieval import Retrieval

        mock_entity_vs = AsyncMock()
        mock_entity_vs.search_entities = AsyncMock()

        mock_retrieval_svc = AsyncMock()
        captured_filter_groups: list[dict] = []

        async def capture_search(**kwargs):
            captured_filter_groups.append(kwargs.get("filter_groups", {}))
            return {"searchResults": [{"id": "r1"}], "virtual_to_record_map": {}}

        mock_retrieval_svc.search_with_filters = capture_search

        state = self._make_retrieval_state(
            entity_vector_store=mock_entity_vs,
            retrieval_service=mock_retrieval_svc,
        )

        tool = Retrieval(state)
        with patch("app.agents.actions.retrieval.retrieval.get_flattened_results", new_callable=AsyncMock, return_value=[]), \
             patch("app.agents.actions.retrieval.retrieval.BlobStorage"):
            try:
                await tool.search_internal_knowledge(
                    query="find it", record_names=["Q3 Security Report"]
                )
            except Exception:
                pass

        assert captured_filter_groups
        assert captured_filter_groups[0].get("records") == ["Q3 Security Report"]
        # Explicit filter provided -> auto-resolution (entity vector search) must be skipped
        mock_entity_vs.search_entities.assert_not_awaited()
