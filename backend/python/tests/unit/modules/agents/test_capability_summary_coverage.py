"""
Coverage tests for app.modules.agents.capability_summary

Targets all pure/near-pure helper functions to push overall coverage past 91.5%.
"""

import pytest
from unittest.mock import MagicMock, patch

from app.modules.agents.capability_summary import (
    _deduplicate_task_ids,
    _extract_domain_note,
    _label_slug,
    _unique_connector_slug,
    build_capability_summary,
    build_connector_routing_rules,
    classify_knowledge_sources,
    format_connector_filter_lines,
)


# ---------------------------------------------------------------------------
# format_connector_filter_lines
# ---------------------------------------------------------------------------

class TestFormatConnectorFilterLines:
    def test_none_filters(self):
        assert format_connector_filter_lines(None) == []

    def test_empty_dict(self):
        assert format_connector_filter_lines({}) == []

    def test_sync_list_filter(self):
        filters = {
            "sync": {
                "projects": {
                    "type": "list",
                    "value": [
                        {"label": "Project A", "id": "pa"},
                        {"label": "Project B", "id": "pb"},
                    ],
                }
            }
        }
        lines = format_connector_filter_lines(filters)
        assert len(lines) == 1
        assert "Scoped to:" in lines[0]
        assert "Project A" in lines[0]
        assert "Project B" in lines[0]

    def test_indexing_boolean_filter(self):
        filters = {
            "indexing": {
                "comments": {"type": "boolean", "value": True},
                "attachments": {"type": "boolean", "value": True},
                "enable_ocr": {"type": "boolean", "value": True},  # starts with enable_ → skipped
            }
        }
        lines = format_connector_filter_lines(filters)
        assert len(lines) == 1
        assert "Content indexed:" in lines[0]
        assert "attachments" in lines[0]
        assert "comments" in lines[0]
        assert "enable" not in lines[0]

    def test_non_list_sync_filter_ignored(self):
        filters = {
            "sync": {
                "api_key": {"type": "string", "value": "secret"},
            }
        }
        assert format_connector_filter_lines(filters) == []

    def test_false_indexing_filter_ignored(self):
        filters = {
            "indexing": {
                "comments": {"type": "boolean", "value": False},
            }
        }
        assert format_connector_filter_lines(filters) == []

    def test_sync_with_string_items(self):
        filters = {
            "sync": {
                "spaces": {"type": "list", "value": ["space1", "space2"]},
            }
        }
        lines = format_connector_filter_lines(filters)
        assert "space1" in lines[0]
        assert "space2" in lines[0]

    def test_combined_sync_and_indexing(self):
        filters = {
            "sync": {
                "projects": {"type": "list", "value": [{"label": "Main"}]},
            },
            "indexing": {
                "attachments": {"type": "boolean", "value": True},
            },
        }
        lines = format_connector_filter_lines(filters)
        assert len(lines) == 2

    def test_empty_list_value_skipped(self):
        filters = {"sync": {"projects": {"type": "list", "value": []}}}
        assert format_connector_filter_lines(filters) == []


# ---------------------------------------------------------------------------
# classify_knowledge_sources
# ---------------------------------------------------------------------------

def _split(sources: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split the unified classify_knowledge_sources() list back into
    (kb, apps) purely for test readability."""
    kb = [s for s in sources if s["source_type"] == "kb"]
    apps = [s for s in sources if s["source_type"] == "app"]
    return kb, apps


class TestClassifyKnowledgeSources:
    def test_empty_list(self):
        assert classify_knowledge_sources([]) == []

    def test_none_list(self):
        assert classify_knowledge_sources(None) == []

    def test_kb_entry(self):
        knowledge = [{"type": "KB", "displayName": "My Docs", "connectorId": "kb1"}]
        kb, conn = _split(classify_knowledge_sources(knowledge))
        assert len(kb) == 1
        assert kb[0]["label"] == "My Docs"
        assert kb[0]["connector_id"] == "kb1"

    def test_kb_entry_no_connector_id(self):
        knowledge = [{"type": "KB", "name": "KB Store"}]
        kb, conn = _split(classify_knowledge_sources(knowledge))
        assert kb[0]["connector_id"] == ""

    def test_kb_entry_legacy_filters_ignored(self):
        knowledge = [{"type": "KB", "name": "KB", "connectorId": "kb2", "filters": "not json"}]
        kb, conn = _split(classify_knowledge_sources(knowledge))
        assert kb[0]["connector_id"] == "kb2"

    def test_connector_entry(self):
        knowledge = [{"type": "Confluence", "displayName": "Confluence Eng", "connectorId": "c1"}]
        kb, conn = _split(classify_knowledge_sources(knowledge))
        assert len(conn) == 1
        assert conn[0]["label"] == "Confluence Eng"
        assert conn[0]["connector_id"] == "c1"
        assert conn[0]["type_key"] == "confluence"

    def test_connector_with_config(self):
        knowledge = [{"type": "Jira", "name": "Jira", "connectorId": "j1"}]
        configs = {"j1": {"sync": {"projects": {"type": "list", "value": [{"label": "PROJ"}]}}}}
        kb, conn = _split(classify_knowledge_sources(knowledge, connector_configs=configs))
        assert conn[0].get("filters") == configs["j1"]

    def test_mixed_entries(self):
        knowledge = [
            {"type": "KB", "name": "My KB"},
            {"type": "Confluence", "connectorId": "c1"},
            {"type": "Jira", "name": "Jira", "connectorId": "j1"},
        ]
        kb, conn = _split(classify_knowledge_sources(knowledge))
        assert len(kb) == 1
        assert len(conn) == 2

    def test_non_dict_items_skipped(self):
        knowledge = [None, "invalid", {"type": "KB", "name": "Valid"}]
        kb, conn = _split(classify_knowledge_sources(knowledge))
        assert len(kb) == 1


# ---------------------------------------------------------------------------
# _label_slug, _unique_connector_slug, _deduplicate_task_ids
# ---------------------------------------------------------------------------

class TestSlugHelpers:
    def test_label_slug_basic(self):
        assert _label_slug("Confluence Engineering") == "confluence_engineering"

    def test_label_slug_special_chars(self):
        assert _label_slug("My/Team-Docs!") == "my_team_docs"

    def test_label_slug_empty(self):
        assert _label_slug("") == ""

    def test_unique_connector_slug_with_label(self):
        conn = {"label": "Confluence", "type_key": "confluence"}
        assert _unique_connector_slug(conn, 0) == "confluence"

    def test_unique_connector_slug_no_label(self):
        conn = {"label": "", "type_key": "jira"}
        assert _unique_connector_slug(conn, 0) == "jira"

    def test_unique_connector_slug_no_label_no_type(self):
        conn = {"label": "", "type_key": ""}
        assert _unique_connector_slug(conn, 3) == "connector_3"

    def test_deduplicate_unique_slugs(self):
        connectors = [
            {"label": "Confluence", "type_key": "confluence"},
            {"label": "Jira", "type_key": "jira"},
        ]
        result = _deduplicate_task_ids(connectors)
        assert result == ["confluence", "jira"]

    def test_deduplicate_duplicate_slugs(self):
        connectors = [
            {"label": "Confluence", "type_key": "confluence"},
            {"label": "Confluence", "type_key": "confluence"},
        ]
        result = _deduplicate_task_ids(connectors)
        assert result == ["confluence_1", "confluence_2"]


# ---------------------------------------------------------------------------
# build_connector_routing_rules
# ---------------------------------------------------------------------------

class TestBuildConnectorRoutingRules:
    def test_empty_both_returns_fallback(self):
        result = build_connector_routing_rules([], kb_only_note="Nothing here")
        assert result == "Nothing here"

    def test_kb_only(self):
        kb = [{"label": "My KB", "connector_id": "rg1", "type_key": "", "source_type": "kb"}]
        result = build_connector_routing_rules(kb)
        assert "KB" in result
        assert "My KB" in result
        assert "rg1" in result
        assert "KB-only" in result

    def test_kb_without_connector_ids(self):
        kb = [{"label": "General KB", "connector_id": "", "type_key": "", "source_type": "kb"}]
        result = build_connector_routing_rules(kb)
        assert "omit connector_ids" in result

    def test_connectors_only(self):
        connectors = [{"label": "Confluence", "type_key": "confluence", "connector_id": "c1", "source_type": "app"}]
        result = build_connector_routing_rules(connectors)
        assert "Confluence" in result
        assert "c1" in result
        assert "connector_ids" in result

    def test_mixed_kb_and_connectors(self):
        connectors = [{"label": "Jira", "type_key": "jira", "connector_id": "j1", "source_type": "app"}]
        kb = [{"label": "KB Docs", "connector_id": "rg1", "type_key": "", "source_type": "kb"}]
        result = build_connector_routing_rules(connectors + kb)
        assert "Jira" in result
        assert "KB Docs" in result

    def test_same_type_duplicates(self):
        connectors = [
            {"label": "Confluence Eng", "type_key": "confluence", "connector_id": "c1", "source_type": "app"},
            {"label": "Confluence Sales", "type_key": "confluence", "connector_id": "c2", "source_type": "app"},
        ]
        result = build_connector_routing_rules(connectors)
        assert "c1" in result
        assert "c2" in result
        assert "multiple connectors" in result.lower() or "⚠️" in result

    def test_orchestrator_format(self):
        connectors = [{"label": "Jira", "type_key": "jira", "connector_id": "j1", "source_type": "app"}]
        result = build_connector_routing_rules(connectors, call_format="orchestrator")
        assert "task_id" in result or "description" in result

    def test_planner_format(self):
        connectors = [{"label": "Jira", "type_key": "jira", "connector_id": "j1", "source_type": "app"}]
        result = build_connector_routing_rules(connectors, call_format="planner")
        assert "search_internal_knowledge" in result

    def test_connector_with_filters(self):
        connectors = [{
            "label": "Confluence",
            "type_key": "confluence",
            "connector_id": "c1",
            "source_type": "app",
            "filters": {"sync": {"spaces": {"type": "list", "value": [{"label": "Engineering"}]}}},
        }]
        result = build_connector_routing_rules(connectors)
        assert "Scoped to" in result or "Engineering" in result


# ---------------------------------------------------------------------------
# _extract_domain_note
# ---------------------------------------------------------------------------

class TestExtractDomainNote:
    def test_empty_description(self):
        assert _extract_domain_note("") == ""

    def test_use_this_to_marker(self):
        desc = "Calculator tool. Use this to perform arithmetic calculations. More info."
        result = _extract_domain_note(desc)
        assert "arithmetic" in result

    def test_useful_for_marker(self):
        desc = "Image tool. Useful for generating images from text descriptions."
        result = _extract_domain_note(desc)
        assert "generating images" in result

    def test_first_sentence_fallback(self):
        desc = "Execute Python code in a sandboxed environment. Supports any Python library."
        result = _extract_domain_note(desc)
        assert "Execute Python code" in result

    def test_long_description_truncated(self):
        desc = "A" * 200 + ". Next sentence."
        result = _extract_domain_note(desc, max_chars=50)
        assert len(result) <= 50


# ---------------------------------------------------------------------------
# build_capability_summary
# ---------------------------------------------------------------------------

class TestBuildCapabilitySummary:
    def test_empty_state(self):
        state = {}
        result = build_capability_summary(state)
        assert "Capability Summary" in result

    def test_with_knowledge(self):
        state = {
            "has_knowledge": True,
            "agent_knowledge": [{"type": "KB", "name": "My KB"}],
        }
        result = build_capability_summary(state)
        assert "Knowledge Sources" in result
        assert "My KB" in result

    def test_without_knowledge(self):
        state = {"has_knowledge": False}
        result = build_capability_summary(state)
        assert "No knowledge sources configured" in result

    def test_with_tools(self):
        state = {"tools": ["jira.search_issues", "jira.get_issue"]}
        result = build_capability_summary(state)
        assert "Actions" in result or "actions" in result.lower()

    def test_with_knowledge_and_tools(self):
        state = {
            "has_knowledge": True,
            "agent_knowledge": [{"type": "Confluence", "connectorId": "c1", "name": "Confluence"}],
            "tools": ["confluence.search_pages"],
        }
        result = build_capability_summary(state)
        assert "Confluence" in result
