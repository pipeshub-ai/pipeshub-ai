"""Tests for Jira enrichment field registry."""

from app.connectors.sources.atlassian.jira.enrichment.field_registry import (
    EXCLUDED_SYSTEM_FIELDS,
    LIVE_SYSTEM_FIELDS,
    build_search_field_list,
    system_field_label,
)


class TestFieldRegistry:
    def test_live_system_fields_are_unique(self):
        assert len(LIVE_SYSTEM_FIELDS) == len(set(LIVE_SYSTEM_FIELDS))

    def test_excluded_fields_not_in_search_list(self):
        fields = build_search_field_list({"sprint": "customfield_10110"})
        assert EXCLUDED_SYSTEM_FIELDS.isdisjoint(set(fields))

    def test_build_search_field_list_includes_discovered_custom_ids(self):
        fields = build_search_field_list({"sprint": "customfield_10110", "story_points": "customfield_10016"})
        assert "customfield_10110" in fields
        assert "customfield_10016" in fields
        assert "labels" in fields
        assert "status" not in fields

    def test_system_field_label_mappings(self):
        assert system_field_label("issuetype") == "Type"
        assert system_field_label("duedate") == "Due Date"
        assert system_field_label("fixVersions") == "Fix Versions"
        assert system_field_label("resolution") == "Resolution"

    def test_build_search_field_list_includes_resolution(self):
        fields = build_search_field_list({})
        assert "resolution" in fields
