"""
Unit tests for app.modules.agents.capability_summary

Tests build_capability_summary, _build_knowledge_section,
_build_actions_section, and _get_service_tool_domains.
All functions are pure — no external dependencies.
"""

import json
from typing import Any

import pytest

from app.modules.agents.capability_summary import (
    _build_actions_section,
    _build_knowledge_section,
    _get_service_tool_domains,
    build_capability_summary,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(**overrides) -> dict[str, Any]:
    """Create a minimal state dict with sensible defaults."""
    state = {
        "has_knowledge": False,
        "agent_knowledge": [],
        "tools": [],
        "agent_toolsets": [],
    }
    state.update(overrides)
    return state


# ============================================================================
# 1. build_capability_summary
# ============================================================================

class TestBuildCapabilitySummary:
    """Tests for build_capability_summary()."""

    def test_basic_structure(self):
        """Summary starts with ## Capability Summary header."""
        state = _make_state()
        result = build_capability_summary(state)
        assert result.startswith("## Capability Summary")

    def test_includes_knowledge_section(self):
        """Summary includes ### Knowledge Sources section."""
        state = _make_state()
        result = build_capability_summary(state)
        assert "### Knowledge Sources" in result

    def test_includes_actions_section(self):
        """Summary includes ### Available Actions section."""
        state = _make_state()
        result = build_capability_summary(state)
        assert "### Available Actions" in result

    def test_ends_with_guidance_text(self):
        """Summary ends with guidance about capability questions."""
        state = _make_state()
        result = build_capability_summary(state)
        assert "Do not call tools to answer capability questions" in result

    def test_no_knowledge_no_tools(self):
        """With no knowledge and no tools, both sections say 'not configured'."""
        state = _make_state()
        result = build_capability_summary(state)
        assert "No knowledge sources configured" in result
        assert "No tools configured" in result

    def test_with_knowledge_sources(self):
        """Knowledge sources are listed when configured."""
        state = _make_state(
            has_knowledge=True,
            agent_knowledge=[
                {
                    "name": "Engineering Docs",
                    "type": "googledrive",
                    "connectorId": "conn-123",
                }
            ],
        )
        result = build_capability_summary(state)
        assert "Engineering Docs" in result
        assert "No knowledge sources configured" not in result

    def test_with_tools(self):
        """Tools are listed when configured."""
        state = _make_state(
            tools=["slack.send_message", "slack.list_channels"],
        )
        result = build_capability_summary(state)
        assert "Slack" in result

    def test_with_knowledge_and_tools(self):
        """Both knowledge and tools sections are populated."""
        state = _make_state(
            has_knowledge=True,
            agent_knowledge=[{"name": "Wiki", "type": "confluence", "connectorId": "c1"}],
            tools=["jira.create_issue"],
        )
        result = build_capability_summary(state)
        assert "Wiki" in result
        assert "Jira" in result

    def test_with_knowledge_shows_retrieval_action(self):
        """When knowledge is configured, retrieval actions appear."""
        state = _make_state(has_knowledge=True)
        result = build_capability_summary(state)
        assert "Retrieval" in result


# ============================================================================
# 2. _build_knowledge_section
# ============================================================================

class TestBuildKnowledgeSection:
    """Tests for _build_knowledge_section()."""

    def test_no_knowledge(self):
        """When has_knowledge is False, shows 'No knowledge sources configured'."""
        state = _make_state()
        parts = []
        _build_knowledge_section(state=state, has_knowledge=False, parts=parts)
        text = "\n".join(parts)
        assert "No knowledge sources configured" in text

    def test_has_knowledge_but_empty_list(self):
        """has_knowledge=True but empty agent_knowledge shows internal sources."""
        state = _make_state(has_knowledge=True, agent_knowledge=[])
        parts = []
        _build_knowledge_section(state=state, has_knowledge=True, parts=parts)
        text = "\n".join(parts)
        assert "Internal knowledge sources configured" in text

    def test_knowledge_with_name_and_type(self):
        """KB with name and type is displayed with type."""
        state = _make_state(
            has_knowledge=True,
            agent_knowledge=[
                {"name": "HR Policies", "type": "googledrive", "connectorId": "c1"}
            ],
        )
        parts = []
        _build_knowledge_section(state=state, has_knowledge=True, parts=parts)
        text = "\n".join(parts)
        assert "HR Policies" in text
        assert "googledrive" in text

    def test_knowledge_with_kb_type_shows_collection(self):
        """KB type is displayed as 'Collection'."""
        state = _make_state(
            has_knowledge=True,
            agent_knowledge=[
                {"name": "My KB", "type": "KB", "connectorId": "c1"}
            ],
        )
        parts = []
        _build_knowledge_section(state=state, has_knowledge=True, parts=parts)
        text = "\n".join(parts)
        assert "Collection" in text
        assert "My KB" in text

    def test_knowledge_with_display_name_fallback(self):
        """Falls back to displayName when name is missing."""
        state = _make_state(
            has_knowledge=True,
            agent_knowledge=[
                {"displayName": "Backup Docs", "type": "onedrive", "connectorId": "c1"}
            ],
        )
        parts = []
        _build_knowledge_section(state=state, has_knowledge=True, parts=parts)
        text = "\n".join(parts)
        assert "Backup Docs" in text

    def test_knowledge_unnamed_fallback(self):
        """Falls back to 'Unnamed' when both name and displayName are missing."""
        state = _make_state(
            has_knowledge=True,
            agent_knowledge=[{"type": "slack", "connectorId": "c1"}],
        )
        parts = []
        _build_knowledge_section(state=state, has_knowledge=True, parts=parts)
        text = "\n".join(parts)
        assert "Unnamed" in text

    def test_knowledge_with_record_groups_filter(self):
        """KB with recordGroups in filters shows browse hints."""
        state = _make_state(
            has_knowledge=True,
            agent_knowledge=[
                {
                    "name": "KB Source",
                    "type": "KB",
                    "connectorId": "c1",
                    "filters": {"recordGroups": ["rg-123"]},
                }
            ],
        )
        parts = []
        _build_knowledge_section(state=state, has_knowledge=True, parts=parts)
        text = "\n".join(parts)
        assert "rg-123" in text
        assert "list_files" in text
        assert "parent_type=\"recordGroup\"" in text

    def test_knowledge_with_connector_id_browse_hint(self):
        """Non-KB knowledge with connectorId shows app-level browse hints."""
        state = _make_state(
            has_knowledge=True,
            agent_knowledge=[
                {
                    "name": "Drive",
                    "type": "googledrive",
                    "connectorId": "conn-456",
                }
            ],
        )
        parts = []
        _build_knowledge_section(state=state, has_knowledge=True, parts=parts)
        text = "\n".join(parts)
        assert "conn-456" in text
        assert "parent_type=\"app\"" in text

    def test_knowledge_with_json_string_filters(self):
        """Filters stored as JSON string are parsed correctly."""
        filters_str = json.dumps({"recordGroups": ["rg-789"]})
        state = _make_state(
            has_knowledge=True,
            agent_knowledge=[
                {
                    "name": "KB2",
                    "type": "KB",
                    "connectorId": "c2",
                    "filters": filters_str,
                }
            ],
        )
        parts = []
        _build_knowledge_section(state=state, has_knowledge=True, parts=parts)
        text = "\n".join(parts)
        assert "rg-789" in text

    def test_knowledge_with_invalid_json_string_filters(self):
        """Invalid JSON string filters are handled gracefully."""
        state = _make_state(
            has_knowledge=True,
            agent_knowledge=[
                {
                    "name": "KB3",
                    "type": "KB",
                    "connectorId": "c3",
                    "filters": "not-valid-json",
                }
            ],
        )
        parts = []
        _build_knowledge_section(state=state, has_knowledge=True, parts=parts)
        text = "\n".join(parts)
        # Should not crash, just no browse hints from filters
        assert "KB3" in text

    def test_non_dict_knowledge_items_skipped(self):
        """Non-dict items in knowledge list are skipped without crashing."""
        state = _make_state(
            has_knowledge=True,
            agent_knowledge=["not-a-dict", 42, None],
        )
        parts = []
        _build_knowledge_section(state=state, has_knowledge=True, parts=parts)
        text = "\n".join(parts)
        # Should not crash; the list is truthy but all items are skipped
        # by the `isinstance(kb, dict)` check, so no KB names appear
        assert "### Knowledge Sources" in text
        assert "Can search indexed documents" in text
        # None of the non-dict items should appear as knowledge source names
        assert "not-a-dict" not in text

    def test_search_capability_always_shown_with_knowledge(self):
        """'Can search indexed documents' is always shown when has_knowledge."""
        state = _make_state(
            has_knowledge=True,
            agent_knowledge=[{"name": "Test", "type": "test", "connectorId": "c1"}],
        )
        parts = []
        _build_knowledge_section(state=state, has_knowledge=True, parts=parts)
        text = "\n".join(parts)
        assert "Can search indexed documents" in text

    def test_important_note_when_knowledge_and_tools_overlap(self):
        """IMPORTANT note shown when knowledge sources and service tools coexist."""
        state = _make_state(
            has_knowledge=True,
            agent_knowledge=[
                {"name": "GDrive", "type": "googledrive", "connectorId": "c1"}
            ],
            tools=["googledrive.search_files"],
        )
        parts = []
        _build_knowledge_section(state=state, has_knowledge=True, parts=parts)
        text = "\n".join(parts)
        assert "IMPORTANT" in text
        assert "knowledgehub.list_files" in text

    def test_no_important_note_when_no_tools(self):
        """No IMPORTANT note when there are no service tools."""
        state = _make_state(
            has_knowledge=True,
            agent_knowledge=[
                {"name": "GDrive", "type": "googledrive", "connectorId": "c1"}
            ],
            tools=[],
        )
        parts = []
        _build_knowledge_section(state=state, has_knowledge=True, parts=parts)
        text = "\n".join(parts)
        assert "IMPORTANT" not in text

    def test_knowledge_none_list(self):
        """None agent_knowledge with has_knowledge=True shows internal sources."""
        state = _make_state(has_knowledge=True, agent_knowledge=None)
        parts = []
        _build_knowledge_section(state=state, has_knowledge=True, parts=parts)
        text = "\n".join(parts)
        assert "Internal knowledge sources configured" in text


# ============================================================================
# 3. _build_actions_section
# ============================================================================

class TestBuildActionsSection:
    """Tests for _build_actions_section()."""

    def test_no_tools_no_knowledge(self):
        """With no tools and no knowledge, shows 'No tools configured'."""
        state = _make_state()
        parts = []
        _build_actions_section(state=state, has_knowledge=False, parts=parts)
        text = "\n".join(parts)
        assert "No tools configured" in text

    def test_with_knowledge_shows_retrieval_and_knowledgehub(self):
        """With knowledge, retrieval and knowledgehub actions appear."""
        state = _make_state(has_knowledge=True)
        parts = []
        _build_actions_section(state=state, has_knowledge=True, parts=parts)
        text = "\n".join(parts)
        assert "Retrieval" in text
        assert "Knowledgehub" in text

    def test_tools_grouped_by_domain(self):
        """Tools are grouped by domain (prefix before dot)."""
        state = _make_state(
            tools=["slack.send_message", "slack.list_channels", "jira.create_issue"],
        )
        parts = []
        _build_actions_section(state=state, has_knowledge=False, parts=parts)
        text = "\n".join(parts)
        assert "Slack" in text
        assert "Jira" in text

    def test_tool_names_formatted_with_underscores_replaced(self):
        """Tool names have underscores replaced with spaces in display."""
        state = _make_state(tools=["slack.send_message"])
        parts = []
        _build_actions_section(state=state, has_knowledge=False, parts=parts)
        text = "\n".join(parts)
        assert "send message" in text

    def test_domain_title_cased(self):
        """Domain names are title-cased in display."""
        state = _make_state(tools=["google_drive.search_files"])
        parts = []
        _build_actions_section(state=state, has_knowledge=False, parts=parts)
        text = "\n".join(parts)
        assert "Google Drive" in text

    def test_with_knowledge_and_tools(self):
        """Both retrieval and user tools are shown when knowledge is configured."""
        state = _make_state(
            has_knowledge=True,
            tools=["slack.send_message"],
        )
        parts = []
        _build_actions_section(state=state, has_knowledge=True, parts=parts)
        text = "\n".join(parts)
        assert "Retrieval" in text
        assert "Slack" in text

    def test_domains_sorted_alphabetically(self):
        """Domains appear sorted alphabetically."""
        state = _make_state(
            tools=["z_tool.action", "a_tool.action"],
        )
        parts = []
        _build_actions_section(state=state, has_knowledge=False, parts=parts)
        text = "\n".join(parts)
        # 'A Tool' should appear before 'Z Tool'
        a_pos = text.index("A Tool")
        z_pos = text.index("Z Tool")
        assert a_pos < z_pos


# ============================================================================
# 4. _get_service_tool_domains
# ============================================================================

class TestGetServiceToolDomains:
    """Tests for _get_service_tool_domains()."""

    def test_empty_tools_list(self):
        """Empty tools list returns empty dict."""
        state = _make_state(tools=[])
        assert _get_service_tool_domains(state) == {}

    def test_none_tools(self):
        """None tools returns empty dict."""
        state = _make_state(tools=None)
        assert _get_service_tool_domains(state) == {}

    def test_tools_grouped_by_domain(self):
        """Tools are grouped by domain prefix."""
        state = _make_state(
            tools=["slack.send_message", "slack.list_channels", "jira.create_issue"],
        )
        domains = _get_service_tool_domains(state)
        assert "slack" in domains
        assert "jira" in domains
        assert len(domains["slack"]) == 2
        assert len(domains["jira"]) == 1

    def test_tools_without_dot_skipped(self):
        """Tools without a dot separator are skipped."""
        state = _make_state(tools=["calculator", "datetime"])
        domains = _get_service_tool_domains(state)
        assert domains == {}

    def test_non_string_tools_skipped(self):
        """Non-string items in tools list are skipped."""
        state = _make_state(tools=[42, None, {"name": "test"}, "slack.send"])
        domains = _get_service_tool_domains(state)
        assert "slack" in domains
        assert len(domains["slack"]) == 1

    def test_fallback_to_agent_toolsets(self):
        """Falls back to agent_toolsets when tools list produces no domains."""
        state = _make_state(
            tools=[],  # Empty, triggers fallback
            agent_toolsets=[
                {
                    "name": "googledrive",
                    "tools": [
                        {"fullName": "googledrive.search_files"},
                        {"name": "list_files"},
                    ],
                }
            ],
        )
        domains = _get_service_tool_domains(state)
        assert "googledrive" in domains
        assert "googledrive.search_files" in domains["googledrive"]
        assert "googledrive.list_files" in domains["googledrive"]

    def test_toolsets_with_selected_tools_fallback(self):
        """Uses selectedTools when tools array is empty in toolset."""
        state = _make_state(
            tools=[],
            agent_toolsets=[
                {
                    "name": "slack",
                    "tools": [],
                    "selectedTools": ["send_message", "slack.list_channels"],
                }
            ],
        )
        domains = _get_service_tool_domains(state)
        assert "slack" in domains
        # "send_message" becomes "slack.send_message"
        assert "slack.send_message" in domains["slack"]
        # "slack.list_channels" already has dot, kept as-is
        assert "slack.list_channels" in domains["slack"]

    def test_toolsets_non_dict_skipped(self):
        """Non-dict toolset entries are skipped."""
        state = _make_state(
            tools=[],
            agent_toolsets=["not-a-dict", 42, None],
        )
        domains = _get_service_tool_domains(state)
        assert domains == {}

    def test_toolsets_without_name_skipped(self):
        """Toolsets without 'name' field are skipped."""
        state = _make_state(
            tools=[],
            agent_toolsets=[{"tools": [{"fullName": "x.y"}]}],
        )
        domains = _get_service_tool_domains(state)
        assert domains == {}

    def test_toolsets_empty_tools_and_no_selected_tools(self):
        """Toolset with neither tools nor selectedTools produces nothing."""
        state = _make_state(
            tools=[],
            agent_toolsets=[{"name": "empty", "tools": []}],
        )
        domains = _get_service_tool_domains(state)
        assert domains == {}

    def test_primary_tools_takes_precedence(self):
        """When state['tools'] yields domains, toolsets fallback is not used."""
        state = _make_state(
            tools=["slack.send_message"],
            agent_toolsets=[
                {
                    "name": "jira",
                    "tools": [{"fullName": "jira.create_issue"}],
                }
            ],
        )
        domains = _get_service_tool_domains(state)
        assert "slack" in domains
        # Jira should NOT be included because primary path yielded results
        assert "jira" not in domains

    def test_toolsets_with_full_name_in_tools(self):
        """fullName field in tools array is used directly."""
        state = _make_state(
            tools=[],
            agent_toolsets=[
                {
                    "name": "confluence",
                    "tools": [
                        {"fullName": "confluence.search_pages"},
                    ],
                }
            ],
        )
        domains = _get_service_tool_domains(state)
        assert "confluence" in domains
        assert "confluence.search_pages" in domains["confluence"]

    def test_toolsets_tool_dict_with_name_no_full_name(self):
        """Tool dict with 'name' but no 'fullName' constructs full name from toolset."""
        state = _make_state(
            tools=[],
            agent_toolsets=[
                {
                    "name": "github",
                    "tools": [
                        {"name": "create_pr"},
                    ],
                }
            ],
        )
        domains = _get_service_tool_domains(state)
        assert "github" in domains
        assert "github.create_pr" in domains["github"]

    def test_none_agent_toolsets(self):
        """None agent_toolsets doesn't crash."""
        state = _make_state(tools=[], agent_toolsets=None)
        domains = _get_service_tool_domains(state)
        assert domains == {}

    def test_multiple_toolsets(self):
        """Multiple toolsets are aggregated correctly."""
        state = _make_state(
            tools=[],
            agent_toolsets=[
                {
                    "name": "slack",
                    "tools": [{"fullName": "slack.send_message"}],
                },
                {
                    "name": "jira",
                    "tools": [{"fullName": "jira.create_issue"}],
                },
            ],
        )
        domains = _get_service_tool_domains(state)
        assert "slack" in domains
        assert "jira" in domains
