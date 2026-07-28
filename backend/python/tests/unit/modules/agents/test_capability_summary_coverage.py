"""
Coverage tests for app.modules.agents.capability_summary

Targets all pure/near-pure helper functions to push overall coverage past 91.5%.
"""

import pytest

from app.modules.agents.capability_summary import (
    build_capability_summary,
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
# The record model taught in the knowledge section
# ---------------------------------------------------------------------------

from app.modules.agents.context.source_catalog import SourceCatalog


def _knowledge_rendered(tool_names: list[str] | None = None) -> str:
    """Build SourceCatalog.render() output — the merged Knowledge Sources section."""
    catalog = SourceCatalog.from_state({
        "agent_knowledge": [
            {"type": "Confluence", "displayName": "Confluence", "connectorId": "c1"},
        ],
    })
    return catalog.render(tool_names=tool_names)


class TestKnowledgeSectionTeachesTheRecordModel:
    """The record model and tool-per-level map are now in SourceCatalog.render(),
    not in build_capability_summary — that section only shows Available Actions.
    These tests verify the merged Knowledge Sources section still teaches the
    hierarchy."""

    @pytest.mark.parametrize(
        "level", ["app connector", "record groups", "records", "blocks"]
    )
    def test_every_level_is_named(self, level: str) -> None:
        assert level in _knowledge_rendered()

    def test_records_are_shown_to_nest(self) -> None:
        rendered = _knowledge_rendered()
        assert "an epic over its stories" in rendered
        assert "a page over" in rendered

    def test_record_id_is_named_as_the_handle(self) -> None:
        assert "Every record has a Record ID" in _knowledge_rendered()

    @pytest.mark.parametrize(
        "tool_logical,tool_granted",
        [
            ("knowledgegraph__list_files", "knowledgegraph__list_files"),
            ("knowledgegraph__lookup_record", "knowledgegraph__lookup_record"),
            ("knowledgegraph__navigate", "knowledgegraph__navigate"),
        ],
    )
    def test_each_tool_is_mapped_to_a_level(self, tool_logical: str, tool_granted: str) -> None:
        rendered = _knowledge_rendered(tool_names=[tool_logical])
        assert tool_granted in rendered

    def test_navigate_is_described_as_moving_between_levels(self) -> None:
        rendered = _knowledge_rendered()
        assert "navigate" in rendered.lower() and "children" in rendered.lower()

    def test_fetch_record_named_in_canonical_block(self) -> None:
        """fetch_record appears in the canonical block when the tool is granted."""
        rendered = _knowledge_rendered(tool_names=["knowledgegraph__fetch_record"])
        assert "knowledgegraph__fetch_record" in rendered


class TestKnowledgeSectionOwnsOnlyTheRecordModel:
    """Search-is-a-ranked-sample, foothold framing, retry-with-different-
    parameters, and read-the-full-record-when-incomplete each used to be a
    bullet here too — restating guidance `finding_information`
    (`prompt_builder.py`) and `dynamic_fetch_full_record`'s own schema
    already state once. The merged section teaches ONLY the record model and
    the tool-per-level map; everything else lives exactly once elsewhere."""

    def test_does_not_restate_ranked_sample_framing(self) -> None:
        assert "ranked sample, not the full set" not in _knowledge_rendered()

    def test_does_not_restate_foothold_framing(self) -> None:
        assert "foothold" not in _knowledge_rendered()

    def test_does_not_restate_retry_guidance(self) -> None:
        assert "rephrased or more specific search query" not in _knowledge_rendered()

    def test_does_not_restate_read_the_full_document_guidance(self) -> None:
        assert "signal to read more" not in _knowledge_rendered()

    def test_no_knowledge_and_no_tools_omits_capability_summary(self) -> None:
        """Neither half has anything to report — the section is omitted."""
        assert build_capability_summary({"has_knowledge": False}) == ""

    def test_capability_summary_no_longer_contains_knowledge_sources_heading(self) -> None:
        """Knowledge Sources is now in SourceCatalog.render(), not in Capability Summary."""
        summary = build_capability_summary({
            "has_knowledge": True,
            "agent_knowledge": [{"type": "Confluence", "connectorId": "c1"}],
        })
        assert "### Knowledge Sources" not in summary
        assert "## Knowledge Sources" not in summary


# ---------------------------------------------------------------------------
